"""加载、校验并幂等初始化 v1.3 本地商品与订单场景。"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.adapters.sqlalchemy_models import (
    DemoSeedRequestRow,
    MockPaymentRow,
    OrderItemRow,
    OrderRow,
    ShipmentPackageItemRow,
    ShipmentPackageRow,
    ShipmentRow,
    UserRow,
    WorkspaceRow,
    utc_now,
)
from commerce_resolve.business_models import (
    OrderItemRecord,
    OrderRecord,
    ShipmentPackageItemRecord,
    ShipmentPackageRecord,
    ShipmentRecord,
    ShipmentStatus,
)
from commerce_resolve.demo_catalog_models import (
    DemoAssetManifest,
    DemoCatalog,
    DemoCatalogSummary,
    DemoOrderScenario,
    DemoProduct,
    DemoSeedResult,
    DemoSku,
)

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = Path("data/demo/v1.3/catalog.json")


class DemoCatalogError(ValueError):
    """表示可安全映射到 CLI 或 Web 的目录领域错误。"""

    def __init__(self, error_code: str) -> None:
        """保存稳定错误码，不泄露本机路径或数据库细节。"""

        super().__init__(error_code)
        self.error_code = error_code


def _sha256(path: Path) -> str:
    """流式计算本地资源 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_child(root: Path, relative_path: str) -> Path:
    """解析根目录内文件并拒绝绝对路径和符号解析后的逃逸。"""

    candidate = (root / relative_path).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise DemoCatalogError("catalog_path_unsafe")
    return candidate


def _aggregate_packages(
    scenario: DemoOrderScenario,
) -> tuple[ShipmentStatus, str, date | None] | None:
    """从多包裹场景确定性生成兼容订单级物流摘要。"""

    if not scenario.packages:
        return None
    packages = tuple(sorted(scenario.packages, key=lambda item: item.package_id))
    if all(item.status == "delivered" for item in packages):
        status: ShipmentStatus = "delivered"
        representative = packages[-1]
    elif any(item.status in {"in_transit", "delivered"} for item in packages):
        status = "in_transit"
        representative = next(
            (item for item in packages if item.status == "in_transit"),
            packages[-1],
        )
    else:
        status = "preparing"
        representative = packages[0]
    estimates = [
        item.estimated_delivery_at
        for item in packages
        if item.estimated_delivery_at is not None
    ]
    return status, representative.last_event, max(estimates) if estimates else None


class DemoCatalogService:
    """提供目录校验、公开摘要、内存投影和数据库场景初始化。"""

    def __init__(
        self,
        *,
        project_root: Path = DEFAULT_PROJECT_ROOT,
        engine: Engine | None = None,
    ) -> None:
        """保存显式项目根和可选业务 Engine，不在构造时写入数据。"""

        self.project_root = project_root.resolve()
        self.engine = engine
        self._sessions = (
            sessionmaker(engine, expire_on_commit=False) if engine is not None else None
        )

    def load(self, version: str = "v1.3") -> tuple[DemoCatalog, DemoAssetManifest]:
        """严格读取目录与资源清单，并验证每个本地文件摘要。"""

        if version != "v1.3":
            raise DemoCatalogError("catalog_version_unavailable")
        try:
            catalog = DemoCatalog.model_validate_json(
                _safe_child(self.project_root, str(DEFAULT_CATALOG_PATH)).read_text(
                    encoding="utf-8"
                )
            )
            asset_path = _safe_child(self.project_root, catalog.asset_manifest)
            assets = DemoAssetManifest.model_validate_json(
                asset_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            raise DemoCatalogError("catalog_invalid") from None
        if assets.catalog_version != catalog.catalog_version:
            raise DemoCatalogError("catalog_asset_version_mismatch")
        public_root = self.project_root / "frontend/public"
        for asset in assets.assets:
            path = _safe_child(public_root, asset.relative_path)
            if not path.is_file() or _sha256(path) != asset.sha256:
                raise DemoCatalogError("catalog_asset_invalid")
        source_path = _safe_child(self.project_root, assets.generation.source_file)
        if (
            not source_path.is_file()
            or _sha256(source_path) != assets.generation.source_sha256
        ):
            raise DemoCatalogError("catalog_source_invalid")
        asset_ids = {item.asset_id for item in assets.assets}
        if any(product.image_ref not in asset_ids for product in catalog.products):
            raise DemoCatalogError("catalog_product_asset_missing")
        return catalog, assets

    def summary(self, version: str = "v1.3") -> DemoCatalogSummary:
        """返回后台可展示的目录内容和固定数量。"""

        catalog, _assets = self.load(version)
        return DemoCatalogSummary(
            catalog_version=catalog.catalog_version,
            product_count=len(catalog.products),
            sku_count=sum(len(item.skus) for item in catalog.products),
            persona_count=len(catalog.personas),
            scenario_count=len(catalog.scenarios),
            products=catalog.products,
            personas=catalog.personas,
            scenarios=catalog.scenarios,
        )

    def build_order_record(
        self,
        *,
        scenario_id: str,
        user_id: str,
        workspace_id: str,
    ) -> OrderRecord:
        """从同一目录生成游客只读订单，不接触业务数据库。"""

        catalog, assets = self.load()
        scenario = self._scenario(catalog, scenario_id)
        product_by_sku, sku_by_id = self._indexes(catalog)
        asset_by_id = {item.asset_id: item for item in assets.assets}
        timestamp = datetime(2026, 7, 23, 8, 30, tzinfo=UTC)
        items = tuple(
            self._record_item(
                product_by_sku[item.sku],
                sku_by_id[item.sku],
                asset_by_id[product_by_sku[item.sku].image_ref].relative_path,
                item.quantity,
                timestamp,
            )
            for item in scenario.items
        )
        packages = tuple(
            ShipmentPackageRecord(
                package_id=package.package_id,
                carrier=package.carrier,
                tracking_number=package.tracking_number,
                status=package.status,
                last_event=package.last_event,
                estimated_delivery_at=package.estimated_delivery_at,
                items=tuple(
                    ShipmentPackageItemRecord(
                        sku=item.sku,
                        quantity=item.quantity,
                    )
                    for item in package.items
                ),
                updated_at=timestamp,
            )
            for package in scenario.packages
        )
        summary = _aggregate_packages(scenario)
        shipment = (
            ShipmentRecord(
                order_id=scenario.order_id,
                status=summary[0],
                last_event=summary[1],
                estimated_delivery_at=summary[2],
                updated_at=timestamp,
            )
            if summary is not None
            else None
        )
        return OrderRecord(
            order_id=scenario.order_id,
            user_id=user_id,
            workspace_id=workspace_id,
            status=scenario.status,
            shipment=shipment,
            items=items,
            packages=packages,
            demo_scenario_id=scenario.scenario_id,
            catalog_version=catalog.catalog_version,
            created_at=datetime(2026, 7, 21, 9, 0, tzinfo=UTC),
            updated_at=timestamp,
        )

    def seed_for_username(
        self,
        *,
        username: str,
        scenario_id: str,
        version: str = "v1.3",
    ) -> DemoSeedResult:
        """通过可信 CLI 为现有账号初始化场景，不创建管理员幂等记录。"""

        sessions = self._require_sessions()
        with sessions() as session:
            user = session.scalar(
                select(UserRow).where(
                    UserRow.username_normalized == username.strip().lower()
                )
            )
            workspace = (
                session.scalar(
                    select(WorkspaceRow).where(WorkspaceRow.owner_user_id == user.id)
                )
                if user is not None
                else None
            )
        if user is None or workspace is None:
            raise DemoCatalogError("catalog_target_unavailable")
        return self.seed(
            user_id=user.id,
            workspace_id=workspace.id,
            scenario_id=scenario_id,
            version=version,
        )

    def seed(
        self,
        *,
        user_id: str,
        workspace_id: str,
        scenario_id: str,
        version: str = "v1.3",
        admin_user_id: str | None = None,
        client_request_id: str | None = None,
    ) -> DemoSeedResult:
        """原子写入订单快照、包裹和 Mock 支付，并保证请求幂等。"""

        if (admin_user_id is None) != (client_request_id is None):
            raise DemoCatalogError("catalog_seed_request_invalid")
        catalog, assets = self.load(version)
        scenario = self._scenario(catalog, scenario_id)
        product_by_sku, sku_by_id = self._indexes(catalog)
        asset_by_id = {item.asset_id: item for item in assets.assets}
        sessions = self._require_sessions()
        now = utc_now()
        try:
            with sessions.begin() as session:
                user = session.get(UserRow, user_id)
                workspace = session.scalar(
                    select(WorkspaceRow).where(
                        WorkspaceRow.id == workspace_id,
                        WorkspaceRow.owner_user_id == user_id,
                    )
                )
                if user is None or workspace is None:
                    raise DemoCatalogError("catalog_target_unavailable")
                if admin_user_id is not None and client_request_id is not None:
                    previous = session.scalar(
                        select(DemoSeedRequestRow).where(
                            DemoSeedRequestRow.admin_user_id == admin_user_id,
                            DemoSeedRequestRow.client_request_id == client_request_id,
                        )
                    )
                    if previous is not None:
                        if (
                            previous.target_user_id != user_id
                            or previous.workspace_id != workspace_id
                            or previous.scenario_id != scenario.scenario_id
                        ):
                            raise DemoCatalogError("catalog_seed_request_conflict")
                        return DemoSeedResult(
                            catalog_version=catalog.catalog_version,
                            scenario_id=previous.scenario_id,
                            order_id=previous.order_id,
                            created=False,
                        )
                existing = session.scalar(
                    select(OrderRow).where(
                        OrderRow.workspace_id == workspace_id,
                        OrderRow.demo_scenario_id == scenario.scenario_id,
                    )
                )
                if existing is not None:
                    self._record_seed_request(
                        session,
                        admin_user_id=admin_user_id,
                        client_request_id=client_request_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        scenario=scenario,
                        order_id=existing.order_id,
                        now=now,
                    )
                    return DemoSeedResult(
                        catalog_version=catalog.catalog_version,
                        scenario_id=scenario.scenario_id,
                        order_id=existing.order_id,
                        created=False,
                    )
                order = OrderRow(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    order_id=scenario.order_id,
                    user_id=user_id,
                    status=scenario.status,
                    demo_scenario_id=scenario.scenario_id,
                    catalog_version=catalog.catalog_version,
                    created_at=now,
                    updated_at=now,
                )
                session.add(order)
                session.flush()
                item_rows: dict[str, OrderItemRow] = {}
                total = 0
                for scenario_item in scenario.items:
                    product = product_by_sku[scenario_item.sku]
                    sku = sku_by_id[scenario_item.sku]
                    asset = asset_by_id[product.image_ref]
                    row = OrderItemRow(
                        id=str(uuid4()),
                        order_pk=order.id,
                        sku=sku.sku,
                        title=product.title,
                        quantity=scenario_item.quantity,
                        product_category=product.category,
                        product_ref=product.product_ref,
                        variant_title=sku.variant_title,
                        unit_amount_minor=sku.unit_amount_minor,
                        currency=sku.currency,
                        image_ref=asset.relative_path,
                        catalog_version=catalog.catalog_version,
                        created_at=now,
                        updated_at=now,
                    )
                    item_rows[sku.sku] = row
                    total += sku.unit_amount_minor * scenario_item.quantity
                    session.add(row)
                session.flush()
                for package in scenario.packages:
                    package_row = ShipmentPackageRow(
                        id=str(uuid4()),
                        workspace_id=workspace_id,
                        order_pk=order.id,
                        package_id=package.package_id,
                        carrier=package.carrier,
                        tracking_number=package.tracking_number,
                        status=package.status,
                        last_event=package.last_event,
                        estimated_delivery_at=package.estimated_delivery_at,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(package_row)
                    session.flush()
                    session.add_all(
                        ShipmentPackageItemRow(
                            id=str(uuid4()),
                            package_pk=package_row.id,
                            order_item_pk=item_rows[item.sku].id,
                            quantity=item.quantity,
                        )
                        for item in package.items
                    )
                shipment = _aggregate_packages(scenario)
                if shipment is not None:
                    session.add(
                        ShipmentRow(
                            id=str(uuid4()),
                            workspace_id=workspace_id,
                            order_pk=order.id,
                            status=shipment[0],
                            last_event=shipment[1],
                            estimated_delivery_at=shipment[2],
                            created_at=now,
                            updated_at=now,
                        )
                    )
                session.add(
                    MockPaymentRow(
                        id=str(uuid4()),
                        workspace_id=workspace_id,
                        order_pk=order.id,
                        amount_minor=total,
                        currency="CNY",
                        channel=scenario.payment.channel,
                        status=scenario.payment.status,
                        created_at=now,
                        updated_at=now,
                    )
                )
                self._record_seed_request(
                    session,
                    admin_user_id=admin_user_id,
                    client_request_id=client_request_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    scenario=scenario,
                    order_id=order.order_id,
                    now=now,
                )
        except IntegrityError:
            raise DemoCatalogError("catalog_seed_conflict") from None
        return DemoSeedResult(
            catalog_version=catalog.catalog_version,
            scenario_id=scenario.scenario_id,
            order_id=scenario.order_id,
            created=True,
        )

    def _record_seed_request(
        self,
        session: Session,
        *,
        admin_user_id: str | None,
        client_request_id: str | None,
        user_id: str,
        workspace_id: str,
        scenario: DemoOrderScenario,
        order_id: str,
        now: datetime,
    ) -> None:
        """在管理员路径写入请求绑定；可信 CLI 路径不写记录。"""

        if admin_user_id is None or client_request_id is None:
            return
        session.add(
            DemoSeedRequestRow(
                request_id=str(uuid4()),
                admin_user_id=admin_user_id,
                target_user_id=user_id,
                workspace_id=workspace_id,
                client_request_id=client_request_id,
                scenario_id=scenario.scenario_id,
                order_id=order_id,
                created_at=now,
            )
        )

    def _require_sessions(self):
        """返回已配置 Session 工厂，否则拒绝数据库写操作。"""

        if self._sessions is None:
            raise DemoCatalogError("catalog_database_unavailable")
        return self._sessions

    @staticmethod
    def _scenario(catalog: DemoCatalog, scenario_id: str) -> DemoOrderScenario:
        """按稳定标识读取场景，不存在时返回领域错误。"""

        result = next(
            (item for item in catalog.scenarios if item.scenario_id == scenario_id),
            None,
        )
        if result is None:
            raise DemoCatalogError("catalog_scenario_unavailable")
        return result

    @staticmethod
    def _indexes(
        catalog: DemoCatalog,
    ) -> tuple[dict[str, DemoProduct], dict[str, DemoSku]]:
        """构建目录内 SKU 到商品及规格的确定性索引。"""

        products: dict[str, DemoProduct] = {}
        skus: dict[str, DemoSku] = {}
        for product in catalog.products:
            for sku in product.skus:
                products[sku.sku] = product
                skus[sku.sku] = sku
        return products, skus

    @staticmethod
    def _record_item(
        product: DemoProduct,
        sku: DemoSku,
        image_ref: str,
        quantity: int,
        timestamp: datetime,
    ) -> OrderItemRecord:
        """把目录 SKU 转换为不可变的内存订单商品快照。"""

        return OrderItemRecord(
            sku=sku.sku,
            title=product.title,
            quantity=quantity,
            product_category=product.category,
            product_ref=product.product_ref,
            variant_title=sku.variant_title,
            unit_amount_minor=sku.unit_amount_minor,
            currency=sku.currency,
            image_ref=image_ref,
            catalog_version="v1.3",
            created_at=timestamp,
            updated_at=timestamp,
        )
