"""定义 v1.1 售后中心的只读客户投影模型。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from commerce_resolve.business_models import (
    OrderStatus,
    PaymentChannel,
    PaymentCurrency,
    PaymentStatus,
    ProductCategory,
    RefundStatus,
    ShipmentStatus,
)

ServiceKind = Literal["refund", "l2_support"]
ServiceStatus = Literal[
    "waiting_user",
    "in_progress",
    "completed",
    "needs_attention",
    "cancelled",
]
SupportAction = Literal["ask_assistant", "view_policy", "request_refund"]
MilestoneState = Literal["completed", "current", "upcoming"]
SnapshotState = Literal["complete", "partial", "legacy"]


class SupportOrderItem(BaseModel):
    """表示客户订单详情中的一条下单时商品快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    title: str
    quantity: int
    product_category: ProductCategory
    product_ref: str | None = None
    variant_title: str | None = None
    unit_amount: str | None = None
    currency: PaymentCurrency | None = None
    image_url: str | None = None
    image_alt: str | None = None
    snapshot_state: SnapshotState = "legacy"


class SupportProductPreview(BaseModel):
    """表示首页和列表使用的有限商品预览。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    title: str
    variant_title: str | None = None
    quantity: int = Field(ge=1)
    image_url: str | None = None
    image_alt: str


class SupportShipment(BaseModel):
    """表示客户可见的当前物流事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ShipmentStatus
    last_event: str
    estimated_delivery_at: date | None = None
    updated_at: datetime


class SupportShipmentPackageItem(BaseModel):
    """表示包裹内一条客户可识别的商品。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    title: str
    quantity: int = Field(ge=1)


class SupportShipmentPackage(BaseModel):
    """表示订单详情中的一个独立履约包裹。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str
    carrier: str | None = None
    tracking_number: str | None = None
    status: ShipmentStatus
    last_event: str
    estimated_delivery_at: date | None = None
    items: tuple[SupportShipmentPackageItem, ...]
    updated_at: datetime


class SupportShipmentMilestone(BaseModel):
    """表示由当前物流事实确定性投影的一步进度。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Literal["preparing", "in_transit", "delivered"]
    title: str
    state: MilestoneState
    detail: str | None = None
    occurred_at: datetime | None = None


class SupportPayment(BaseModel):
    """表示订单详情中的有限 Mock 支付摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: str
    currency: PaymentCurrency
    channel: PaymentChannel
    status: PaymentStatus


class SupportRefund(BaseModel):
    """表示订单详情中的有限 Mock 退款结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    refund_id: str
    amount: str
    currency: PaymentCurrency
    status: RefundStatus
    updated_at: datetime


class SupportOrderSummary(BaseModel):
    """表示售后首页和订单列表使用的客户订单摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    status: OrderStatus
    item_count: int = Field(ge=0)
    item_title_preview: str | None = None
    preview_items: tuple[SupportProductPreview, ...] = ()
    shipment_status: ShipmentStatus | None = None
    fulfillment_summary: str | None = None
    customer_stage: str | None = None
    estimated_delivery_at: date | None = None
    payment_amount: str | None = None
    latest_service_status: ServiceStatus | None = None
    latest_service_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class SupportAmountSummary(BaseModel):
    """汇总商品展示金额与权威支付退款金额，并保持来源边界。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_subtotal: str | None = None
    paid_amount: str | None = None
    refunded_amount: str | None = None
    currency: PaymentCurrency = "CNY"


class SupportOrderDetail(BaseModel):
    """表示订单详情页所需的全部客户公开事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: SupportOrderSummary
    items: tuple[SupportOrderItem, ...] = ()
    shipment: SupportShipment | None = None
    packages: tuple[SupportShipmentPackage, ...] = ()
    shipment_milestones: tuple[SupportShipmentMilestone, ...] = ()
    payment: SupportPayment | None = None
    refunds: tuple[SupportRefund, ...] = ()
    amount_summary: SupportAmountSummary | None = None
    next_step: str | None = None
    available_actions: tuple[SupportAction, ...]


class PublicServiceStep(BaseModel):
    """表示服务详情时间线中不含内部节点的一步。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    title: str
    state: MilestoneState
    occurred_at: datetime | None = None


class SupportCitation(BaseModel):
    """表示客户可定位但不暴露内部检索轨迹的政策依据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    version: str
    locator: str


class ServiceRecordSummary(BaseModel):
    """表示客户服务进度列表中的稳定只读摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_id: str
    kind: ServiceKind
    status: ServiceStatus
    order_id: str | None = None
    thread_id: str
    title: str
    next_action: str | None = None
    product_preview: SupportProductPreview | None = None
    updated_at: datetime


class ServiceRecordDetail(BaseModel):
    """表示服务详情、公开步骤、结果和有限政策依据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: ServiceRecordSummary
    public_steps: tuple[PublicServiceStep, ...]
    result_summary: str | None = None
    citations: tuple[SupportCitation, ...] = ()


class SupportOverview(BaseModel):
    """表示售后首页的进行中服务和最近订单。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active_services: tuple[ServiceRecordSummary, ...]
    recent_orders: tuple[SupportOrderSummary, ...]
    has_more_orders: bool
    has_more_services: bool


class SupportOrdersPage(BaseModel):
    """表示使用稳定游标分页的客户订单列表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orders: tuple[SupportOrderSummary, ...]
    next_cursor: str | None = None


class SupportServicesPage(BaseModel):
    """表示使用稳定游标分页的客户服务列表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    services: tuple[ServiceRecordSummary, ...]
    next_cursor: str | None = None
