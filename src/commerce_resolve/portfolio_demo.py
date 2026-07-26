"""定义并初始化 v2.0 面试演示工作区的三个稳定业务场景。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_resolve.demo_catalog import (
    DEFAULT_PROJECT_ROOT,
    DemoCatalogError,
    DemoCatalogService,
)
from commerce_resolve.demo_catalog_models import (
    DemoCatalog,
    DemoCatalogAsset,
    DemoOrderScenario,
    DemoProduct,
    DemoSku,
)

from .adapters.sqlalchemy_models import (
    MockPaymentRow,
    OrderItemRow,
    OrderRow,
    ShipmentPackageItemRow,
    ShipmentPackageRow,
    ShipmentRow,
    WorkspaceRow,
)

PORTFOLIO_DATASET_VERSION = "portfolio-demo-v1"
PORTFOLIO_MANIFEST_PATH = Path("data/demo/portfolio-demo-v1.json")
PUBLIC_ORDER_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class PortfolioDataError(ValueError):
    """表示演示数据加载或初始化失败，且不暴露本地路径。"""

    def __init__(self, error_code: str) -> None:
        """保存稳定错误码供注册与重置用例安全映射。"""

        super().__init__(error_code)
        self.error_code = error_code


class PortfolioShipmentDefinition(BaseModel):
    """定义一个场景的基准物流状态与相对时间。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["preparing", "in_transit", "delivered"]
    carrier: str = Field(min_length=1, max_length=80)
    last_event: str = Field(min_length=1, max_length=300)
    estimated_delivery_offset_days: int = Field(ge=-365, le=365)
    event_age_days: int = Field(ge=0, le=365)


class PortfolioScenarioDefinition(BaseModel):
    """定义一个隐藏场景键与既有商品目录场景的映射。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_key: Literal[
        "refundable_delay",
        "quality_issue",
        "expired_refund",
    ]
    source_scenario_id: str = Field(min_length=3, max_length=80)
    order_status: Literal["processing", "shipped", "delivered", "cancelled"]
    order_age_days: int = Field(ge=0, le=365)
    shipment: PortfolioShipmentDefinition


class PortfolioDataset(BaseModel):
    """定义可审计的 v2.0 演示数据版本及三个互补场景。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    dataset_version: Literal["portfolio-demo-v1"]
    source_catalog_version: Literal["v1.3"]
    scenarios: tuple[PortfolioScenarioDefinition, ...] = Field(
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_scenarios(self) -> PortfolioDataset:
        """确保三个约定场景各出现一次。"""

        expected = {"refundable_delay", "quality_issue", "expired_refund"}
        actual = {item.scenario_key for item in self.scenarios}
        if actual != expected or len(actual) != len(self.scenarios):
            raise ValueError("portfolio scenarios must match the v2.0 contract")
        return self


class PortfolioSeedResult(BaseModel):
    """返回初始化后的数据版本和隐藏场景到公开订单号绑定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version: Literal["portfolio-demo-v1"]
    order_ids: dict[str, str]


class PortfolioDemoService:
    """加载版本化场景并在调用方事务中写入完整订单基准事实。"""

    def __init__(self, *, project_root: Path = DEFAULT_PROJECT_ROOT) -> None:
        """保存显式项目根，不在构造时读取文件或产生数据库写入。"""

        self.project_root = project_root.resolve()
        self._catalog = DemoCatalogService(project_root=self.project_root)

    def load(
        self,
    ) -> tuple[PortfolioDataset, DemoCatalog, dict[str, DemoCatalogAsset]]:
        """校验演示 Manifest、源目录、场景引用和商品资源。"""

        try:
            manifest_path = (self.project_root / PORTFOLIO_MANIFEST_PATH).resolve()
            manifest_path.relative_to(self.project_root)
            dataset = PortfolioDataset.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            catalog, assets = self._catalog.load(dataset.source_catalog_version)
        except (OSError, ValidationError, ValueError, DemoCatalogError):
            raise PortfolioDataError("portfolio_dataset_invalid") from None
        source_ids = {item.scenario_id for item in catalog.scenarios}
        if any(item.source_scenario_id not in source_ids for item in dataset.scenarios):
            raise PortfolioDataError("portfolio_source_scenario_missing")
        return dataset, catalog, {item.asset_id: item for item in assets.assets}

    def seed_into_session(
        self,
        session: Session,
        *,
        user_id: str,
        workspace_id: str,
        order_ids: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> PortfolioSeedResult:
        """在调用方事务内写入三笔基准订单，失败时由调用方整体回滚。"""

        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        dataset, catalog, assets = self.load()
        scenarios = {item.scenario_id: item for item in catalog.scenarios}
        product_by_sku, sku_by_id = self._catalog_indexes(catalog)
        assigned: dict[str, str] = {}
        supplied = order_ids or {}
        if set(supplied) - {item.scenario_key for item in dataset.scenarios}:
            raise PortfolioDataError("portfolio_order_binding_invalid")
        for definition in dataset.scenarios:
            public_order_id = supplied.get(definition.scenario_key)
            if public_order_id is None:
                public_order_id = self._new_public_order_id(session, workspace_id)
            self._write_scenario(
                session,
                user_id=user_id,
                workspace_id=workspace_id,
                public_order_id=public_order_id,
                definition=definition,
                source=scenarios[definition.source_scenario_id],
                product_by_sku=product_by_sku,
                sku_by_id=sku_by_id,
                assets=assets,
                now=timestamp,
            )
            assigned[definition.scenario_key] = public_order_id
        workspace = session.get(WorkspaceRow, workspace_id)
        if workspace is None or workspace.owner_user_id != user_id:
            raise PortfolioDataError("portfolio_workspace_unavailable")
        workspace.dataset_version = dataset.dataset_version
        workspace.dataset_status = "ready"
        workspace.initialized_at = timestamp
        return PortfolioSeedResult(
            dataset_version=dataset.dataset_version,
            order_ids=assigned,
        )

    @staticmethod
    def existing_order_ids(
        session: Session,
        *,
        workspace_id: str,
    ) -> dict[str, str]:
        """读取现有三场景的公开订单号，供重置后保持稳定。"""

        rows = session.execute(
            select(OrderRow.demo_scenario_id, OrderRow.order_id).where(
                OrderRow.workspace_id == workspace_id,
                OrderRow.demo_scenario_id.in_(
                    ("refundable_delay", "quality_issue", "expired_refund")
                ),
            )
        ).all()
        return {
            str(scenario_key): str(order_id)
            for scenario_key, order_id in rows
            if scenario_key is not None
        }

    def _new_public_order_id(self, session: Session, workspace_id: str) -> str:
        """生成不编码业务语义且在目标工作区内未使用的公开订单号。"""

        for _attempt in range(32):
            first = "".join(secrets.choice(PUBLIC_ORDER_ALPHABET) for _ in range(4))
            second = "".join(secrets.choice(PUBLIC_ORDER_ALPHABET) for _ in range(4))
            candidate = f"CR-{first}-{second}"
            exists = session.scalar(
                select(OrderRow.id).where(
                    OrderRow.workspace_id == workspace_id,
                    OrderRow.order_id == candidate,
                )
            )
            if exists is None:
                return candidate
        raise PortfolioDataError("portfolio_order_id_exhausted")

    @staticmethod
    def _catalog_indexes(
        catalog: DemoCatalog,
    ) -> tuple[dict[str, DemoProduct], dict[str, DemoSku]]:
        """构建源目录 SKU 到商品和规格的确定性索引。"""

        products: dict[str, DemoProduct] = {}
        skus: dict[str, DemoSku] = {}
        for product in catalog.products:
            for sku in product.skus:
                products[sku.sku] = product
                skus[sku.sku] = sku
        return products, skus

    @staticmethod
    def _write_scenario(
        session: Session,
        *,
        user_id: str,
        workspace_id: str,
        public_order_id: str,
        definition: PortfolioScenarioDefinition,
        source: DemoOrderScenario,
        product_by_sku: dict[str, DemoProduct],
        sku_by_id: dict[str, DemoSku],
        assets: dict[str, DemoCatalogAsset],
        now: datetime,
    ) -> None:
        """写入一条场景的订单、快照、包裹、汇总物流和 Mock 支付。"""

        order_time = now - timedelta(days=definition.order_age_days)
        event_time = now - timedelta(days=definition.shipment.event_age_days)
        order = OrderRow(
            id=str(uuid4()),
            workspace_id=workspace_id,
            order_id=public_order_id,
            user_id=user_id,
            status=definition.order_status,
            demo_scenario_id=definition.scenario_key,
            catalog_version=PORTFOLIO_DATASET_VERSION,
            created_at=order_time,
            updated_at=event_time,
        )
        session.add(order)
        session.flush()
        item_rows: dict[str, OrderItemRow] = {}
        total_amount = 0
        for item in source.items:
            product = product_by_sku[item.sku]
            sku = sku_by_id[item.sku]
            asset = assets[product.image_ref]
            item_row = OrderItemRow(
                id=str(uuid4()),
                order_pk=order.id,
                sku=sku.sku,
                title=product.title,
                quantity=item.quantity,
                product_category=product.category,
                product_ref=product.product_ref,
                variant_title=sku.variant_title,
                unit_amount_minor=sku.unit_amount_minor,
                currency=sku.currency,
                image_ref=asset.relative_path,
                catalog_version=PORTFOLIO_DATASET_VERSION,
                created_at=order_time,
                updated_at=event_time,
            )
            session.add(item_row)
            item_rows[item.sku] = item_row
            total_amount += sku.unit_amount_minor * item.quantity
        session.flush()
        package = ShipmentPackageRow(
            id=str(uuid4()),
            workspace_id=workspace_id,
            order_pk=order.id,
            package_id=f"PKG-{public_order_id.replace('-', '')[-8:]}",
            carrier=definition.shipment.carrier,
            tracking_number=f"MOCK-{public_order_id.replace('-', '')[-8:]}",
            status=definition.shipment.status,
            last_event=definition.shipment.last_event,
            estimated_delivery_at=(
                now.date()
                + timedelta(days=definition.shipment.estimated_delivery_offset_days)
            ),
            created_at=order_time,
            updated_at=event_time,
        )
        session.add(package)
        session.flush()
        session.add_all(
            ShipmentPackageItemRow(
                id=str(uuid4()),
                package_pk=package.id,
                order_item_pk=item_rows[item.sku].id,
                quantity=item.quantity,
            )
            for item in source.items
        )
        session.add(
            ShipmentRow(
                id=str(uuid4()),
                workspace_id=workspace_id,
                order_pk=order.id,
                status=definition.shipment.status,
                last_event=definition.shipment.last_event,
                estimated_delivery_at=(
                    now.date()
                    + timedelta(days=definition.shipment.estimated_delivery_offset_days)
                ),
                created_at=order_time,
                updated_at=event_time,
            )
        )
        session.add(
            MockPaymentRow(
                id=str(uuid4()),
                workspace_id=workspace_id,
                order_pk=order.id,
                amount_minor=total_amount,
                currency="CNY",
                channel=source.payment.channel,
                status="settled",
                created_at=order_time,
                updated_at=event_time,
            )
        )
