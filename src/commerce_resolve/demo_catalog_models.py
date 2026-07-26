"""定义 v1.3 本地商品目录、演示场景与播种结果契约。"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from commerce_resolve.business_models import (
    OrderStatus,
    PaymentChannel,
    ProductCategory,
    ShipmentStatus,
)


class DemoCatalogAsset(BaseModel):
    """描述一个可校验、可公开的本地商品资源。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    relative_path: str = Field(min_length=1, max_length=180)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alt: str = Field(min_length=1, max_length=160)
    source: Literal["generated", "project"]

    @model_validator(mode="after")
    def validate_safe_path(self) -> Self:
        """限制资源位于公开目录的版本子路径，拒绝路径逃逸。"""

        parts = self.relative_path.split("/")
        if (
            parts[:2] != ["catalog", "v1.3"]
            or len(parts) != 3
            or any(part in {"", ".", ".."} for part in parts)
            or not self.relative_path.endswith((".webp", ".svg"))
        ):
            raise ValueError("catalog asset path is outside catalog/v1.3")
        return self


class DemoAssetGeneration(BaseModel):
    """保存原创商品资源的生成工具、源图摘要和 Prompt。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1, max_length=120)
    date: date
    source_file: str = Field(min_length=1, max_length=180)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt: str = Field(min_length=20, max_length=2000)


class DemoAssetManifest(BaseModel):
    """定义一个目录版本对应的本地资源清单。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    catalog_version: Literal["v1.3"]
    generation: DemoAssetGeneration
    assets: tuple[DemoCatalogAsset, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_assets(self) -> Self:
        """拒绝重复资源标识或路径。"""

        ids = [item.asset_id for item in self.assets]
        paths = [item.relative_path for item in self.assets]
        if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("catalog assets must be unique")
        return self


class DemoSku(BaseModel):
    """定义可下单 SKU 的规格和展示单价。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(pattern=r"^[A-Z0-9._-]{1,40}$")
    variant_title: str = Field(min_length=1, max_length=120)
    unit_amount_minor: int = Field(gt=0)
    currency: Literal["CNY"]


class DemoProduct(BaseModel):
    """定义商品主档及其一个或多个 SKU。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_ref: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    title: str = Field(min_length=1, max_length=120)
    category: ProductCategory
    image_ref: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    skus: tuple[DemoSku, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_unique_skus(self) -> Self:
        """拒绝商品内部重复 SKU。"""

        values = [item.sku for item in self.skus]
        if len(values) != len(set(values)):
            raise ValueError("product skus must be unique")
        return self


class DemoPersona(BaseModel):
    """定义场景面向的演示客户画像。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)


class DemoScenarioItem(BaseModel):
    """定义演示订单中的 SKU 和购买数量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(pattern=r"^[A-Z0-9._-]{1,40}$")
    quantity: int = Field(ge=1, le=99)


class DemoScenarioPackage(BaseModel):
    """定义演示订单中的单个包裹和商品分配。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str = Field(pattern=r"^[A-Z0-9._-]{1,64}$")
    carrier: str | None = Field(default=None, max_length=80)
    tracking_number: str | None = Field(default=None, max_length=100)
    status: ShipmentStatus
    last_event: str = Field(min_length=1, max_length=300)
    estimated_delivery_at: date | None = None
    items: tuple[DemoScenarioItem, ...] = Field(min_length=1, max_length=20)


class DemoScenarioPayment(BaseModel):
    """定义演示订单的 Mock 支付渠道和状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: PaymentChannel
    status: Literal["pending", "settled", "failed"]


class DemoOrderScenario(BaseModel):
    """定义一条可幂等初始化的完整 Mock 订单场景。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    persona_id: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")
    status: OrderStatus
    items: tuple[DemoScenarioItem, ...] = Field(min_length=1, max_length=10)
    packages: tuple[DemoScenarioPackage, ...] = Field(default=(), max_length=20)
    payment: DemoScenarioPayment


class DemoCatalog(BaseModel):
    """定义 v1.3 商品、画像与演示场景的唯一事实源。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    catalog_version: Literal["v1.3"]
    asset_manifest: str
    products: tuple[DemoProduct, ...] = Field(min_length=12)
    personas: tuple[DemoPersona, ...] = Field(min_length=3)
    scenarios: tuple[DemoOrderScenario, ...] = Field(min_length=10)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        """校验目录唯一性、场景引用和包裹数量守恒。"""

        if self.asset_manifest != "frontend/public/catalog/v1.3/assets.json":
            raise ValueError("unsupported catalog asset manifest")
        product_ids = [item.product_ref for item in self.products]
        persona_ids = [item.persona_id for item in self.personas]
        scenario_ids = [item.scenario_id for item in self.scenarios]
        order_ids = [item.order_id for item in self.scenarios]
        sku_items = [sku for product in self.products for sku in product.skus]
        sku_ids = [item.sku for item in sku_items]
        for values in (product_ids, persona_ids, scenario_ids, order_ids, sku_ids):
            if len(values) != len(set(values)):
                raise ValueError("catalog identifiers must be unique")
        if len(sku_items) < 18:
            raise ValueError("catalog requires at least 18 skus")
        known_personas = set(persona_ids)
        known_skus = set(sku_ids)
        for scenario in self.scenarios:
            if scenario.persona_id not in known_personas:
                raise ValueError("scenario references an unknown persona")
            purchased = {item.sku: item.quantity for item in scenario.items}
            if len(purchased) != len(scenario.items):
                raise ValueError("scenario order items must be unique")
            if not set(purchased).issubset(known_skus):
                raise ValueError("scenario references an unknown sku")
            shipped: dict[str, int] = {}
            package_ids = [item.package_id for item in scenario.packages]
            if len(package_ids) != len(set(package_ids)):
                raise ValueError("scenario package ids must be unique")
            for package in scenario.packages:
                for item in package.items:
                    if item.sku not in purchased:
                        raise ValueError("package references an unpurchased sku")
                    shipped[item.sku] = shipped.get(item.sku, 0) + item.quantity
                    if shipped[item.sku] > purchased[item.sku]:
                        raise ValueError("package quantity exceeds purchased quantity")
        return self


class DemoCatalogSummary(BaseModel):
    """返回后台目录检查所需的有限版本与数量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    catalog_version: str
    product_count: int = Field(ge=0)
    sku_count: int = Field(ge=0)
    persona_count: int = Field(ge=0)
    scenario_count: int = Field(ge=0)
    products: tuple[DemoProduct, ...]
    personas: tuple[DemoPersona, ...]
    scenarios: tuple[DemoOrderScenario, ...]


class DemoSeedResult(BaseModel):
    """返回演示场景初始化后的稳定订单绑定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    catalog_version: str
    scenario_id: str
    order_id: str
    created: bool
