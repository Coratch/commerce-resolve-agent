"""定义账号、会话、工作区及私有业务数据的领域模型。"""

import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

UserStatus = Literal["active", "disabled"]
UserRole = Literal["customer", "admin"]
WebActorType = Literal["guest", "registered"]
WorkspaceDatasetStatus = Literal["initializing", "ready", "resetting", "failed"]
OrderStatus = Literal["processing", "shipped", "delivered", "cancelled"]
ShipmentStatus = Literal["preparing", "in_transit", "delivered"]
ProductCategory = Literal["general", "apparel", "hygiene", "digital"]
PaymentCurrency = Literal["CNY"]
PaymentChannel = Literal["mock_card", "mock_wallet"]
PaymentStatus = Literal["pending", "settled", "failed", "refunded"]
RefundReasonCode = Literal[
    "no_longer_needed",
    "quality_issue",
    "delivery_issue",
    "other",
]
RefundActionStatus = Literal[
    "awaiting_approval",
    "rejected",
    "stale",
    "executing",
    "completed",
    "failed",
    "unknown",
    "verification_failed",
]
RefundStatus = Literal["processing", "succeeded", "failed", "unknown"]

MONEY_PATTERN = r"^(0|[1-9][0-9]{0,9})\.[0-9]{2}$"
ORDER_ID_PATTERN_TEXT = (
    r"^(?:CR-[23456789A-HJ-NP-Z]{4}-[23456789A-HJ-NP-Z]{4}"
    r"|ORD-[A-Z0-9-]{3,32})$"
)


def amount_to_minor_units(amount: str) -> int:
    """把严格两位小数的金额字符串转换为整数分，拒绝零值和浮点语义。"""

    if re.fullmatch(MONEY_PATTERN, amount) is None:
        raise ValueError("amount must use a two-decimal string")
    major, minor = amount.split(".", maxsplit=1)
    value = int(major) * 100 + int(minor)
    if value <= 0:
        raise ValueError("amount must be positive")
    return value


def format_minor_units(amount_minor: int) -> str:
    """把非负整数分格式化为固定两位小数的公开金额字符串。"""

    if amount_minor < 0:
        raise ValueError("amount_minor cannot be negative")
    major, minor = divmod(amount_minor, 100)
    return f"{major}.{minor:02d}"


class UserAccount(BaseModel):
    """表示可登录账号的公开领域字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    username: str
    status: UserStatus
    role: UserRole = "customer"
    created_at: datetime


class Workspace(BaseModel):
    """表示一个注册用户独占的私有演示工作区。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    owner_user_id: str
    dataset_version: str | None = None
    dataset_status: WorkspaceDatasetStatus | None = None
    reset_generation: int = Field(default=0, ge=0)
    initialized_at: datetime | None = None
    created_at: datetime


class InvitationIssued(BaseModel):
    """返回邀请码创建时唯一可见的明文及元数据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    code: str
    expires_at: datetime
    max_uses: int


class RegistrationResult(BaseModel):
    """保存邀请码注册事务创建的账号和工作区。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user: UserAccount
    workspace: Workspace


class SessionBundle(BaseModel):
    """返回浏览器 Session、CSRF 明文和可信身份。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    session_token: str
    csrf_token: str
    actor_type: WebActorType
    subject_id: str
    user_id: str | None
    workspace_id: str
    username: str | None = None
    user_role: UserRole | None = None
    expires_at: datetime


class SessionIdentity(BaseModel):
    """表示从有效 Session Token 解析出的持久身份记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    actor_type: WebActorType
    subject_id: str
    user_id: str | None
    workspace_id: str
    username: str | None = None
    user_status: UserStatus | None = None
    user_role: UserRole | None = None
    expires_at: datetime


class ConversationRecord(BaseModel):
    """保存 conversation 与服务端身份、工作区的绑定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str
    subject_id: str
    workspace_id: str
    access_mode: WebActorType
    related_order_id: str | None = None
    title: str = "新会话"
    lifecycle_status: Literal["active", "archived", "deleting", "deleted"] = "active"
    history_state: Literal["complete", "partial"] = "complete"
    message_count: int = 0
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    pending_action: str | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ShipmentInput(BaseModel):
    """校验创建或更新物流记录所需字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ShipmentStatus
    last_event: str = Field(min_length=1, max_length=300)
    estimated_delivery_at: date | None = None


class OrderItemInput(BaseModel):
    """校验订单商品快照；金额仅用于展示，退款仍以支付事实为准。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(pattern=r"^[A-Za-z0-9._-]{1,40}$")
    title: str = Field(min_length=1, max_length=120)
    quantity: int = Field(ge=1, le=99)
    product_category: ProductCategory = "general"
    product_ref: str | None = Field(default=None, max_length=80)
    variant_title: str | None = Field(default=None, max_length=120)
    unit_amount_minor: int | None = Field(default=None, ge=0)
    currency: PaymentCurrency | None = None
    image_ref: str | None = Field(default=None, max_length=120)
    catalog_version: str | None = Field(default=None, max_length=40)


class OrderItemRecord(BaseModel):
    """表示从业务数据库读取的一条不可被目录变化改写的商品快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    title: str
    quantity: int
    product_category: ProductCategory
    product_ref: str | None = None
    variant_title: str | None = None
    unit_amount_minor: int | None = None
    currency: PaymentCurrency | None = None
    image_ref: str | None = None
    catalog_version: str | None = None
    created_at: datetime
    updated_at: datetime


class ShipmentPackageItemInput(BaseModel):
    """校验包裹中某个 SKU 的发货数量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(pattern=r"^[A-Za-z0-9._-]{1,40}$")
    quantity: int = Field(ge=1, le=99)


class ShipmentPackageInput(BaseModel):
    """校验一个 Mock 包裹及其商品分配。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    carrier: str | None = Field(default=None, max_length=80)
    tracking_number: str | None = Field(default=None, max_length=100)
    status: ShipmentStatus
    last_event: str = Field(min_length=1, max_length=300)
    estimated_delivery_at: date | None = None
    items: tuple[ShipmentPackageItemInput, ...] = Field(
        min_length=1,
        max_length=20,
    )


class ShipmentPackageItemRecord(BaseModel):
    """表示包裹内一条商品履约快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    quantity: int


class ShipmentPackageRecord(BaseModel):
    """表示客户可见的单个包裹及其商品分配。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str
    carrier: str | None = None
    tracking_number: str | None = None
    status: ShipmentStatus
    last_event: str
    estimated_delivery_at: date | None = None
    items: tuple[ShipmentPackageItemRecord, ...]
    updated_at: datetime


def _validate_unique_items(items: tuple[OrderItemInput, ...]) -> None:
    """拒绝同一订单内大小写不敏感的重复 SKU。"""

    normalized = [item.sku.upper() for item in items]
    if len(normalized) != len(set(normalized)):
        raise ValueError("order item sku must be unique")


class OrderCreate(BaseModel):
    """校验私有工作区中新建订单及可选物流。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(pattern=ORDER_ID_PATTERN_TEXT)
    status: OrderStatus
    shipment: ShipmentInput | None = None
    items: tuple[OrderItemInput, ...] = Field(default=(), max_length=10)
    packages: tuple[ShipmentPackageInput, ...] = Field(default=(), max_length=20)
    demo_scenario_id: str | None = Field(default=None, max_length=80)
    catalog_version: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_items(self) -> Self:
        """限制商品行数量，并校验包裹引用和累计发货数量。"""

        _validate_unique_items(self.items)
        item_quantities = {item.sku.upper(): item.quantity for item in self.items}
        package_ids = [item.package_id.upper() for item in self.packages]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("shipment package id must be unique")
        shipped: dict[str, int] = {}
        for package in self.packages:
            package_skus = [item.sku.upper() for item in package.items]
            if len(package_skus) != len(set(package_skus)):
                raise ValueError("package item sku must be unique")
            for item in package.items:
                sku = item.sku.upper()
                if sku not in item_quantities:
                    raise ValueError("package item must reference an order item")
                shipped[sku] = shipped.get(sku, 0) + item.quantity
                if shipped[sku] > item_quantities[sku]:
                    raise ValueError("package quantity exceeds order quantity")
        return self


class OrderUpdate(BaseModel):
    """校验订单与物流的部分更新，至少要求一个字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: OrderStatus | None = None
    shipment: ShipmentInput | None = None
    remove_shipment: bool = False
    items: tuple[OrderItemInput, ...] | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def validate_has_change(self) -> Self:
        """拒绝没有任何状态或物流变更的空更新。"""

        if (
            self.status is None
            and self.shipment is None
            and not self.remove_shipment
            and self.items is None
        ):
            raise ValueError("order update requires at least one change")
        if self.shipment is not None and self.remove_shipment:
            raise ValueError("shipment and remove_shipment cannot be combined")
        if self.items is not None:
            _validate_unique_items(self.items)
        return self


class ShipmentRecord(BaseModel):
    """表示从业务数据库读取的物流事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    status: ShipmentStatus
    last_event: str
    estimated_delivery_at: date | None = None
    updated_at: datetime


class OrderRecord(BaseModel):
    """表示私有工作区中的订单及可选物流事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    user_id: str
    workspace_id: str
    status: OrderStatus
    shipment: ShipmentRecord | None = None
    items: tuple[OrderItemRecord, ...] = ()
    packages: tuple[ShipmentPackageRecord, ...] = ()
    demo_scenario_id: str | None = None
    catalog_version: str | None = None
    created_at: datetime
    updated_at: datetime


class MockPaymentInput(BaseModel):
    """校验注册用户为演示订单维护的退款前 Mock 支付事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: str = Field(pattern=MONEY_PATTERN)
    currency: PaymentCurrency = "CNY"
    channel: PaymentChannel
    status: Literal["pending", "settled", "failed"]

    @model_validator(mode="after")
    def validate_positive_amount(self) -> Self:
        """确保金额严格大于零并可无损转换为最小货币单位。"""

        amount_to_minor_units(self.amount)
        return self


class MockPaymentRecord(BaseModel):
    """表示从业务数据库读取的一笔原始 Mock 支付。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_id: str
    order_id: str
    amount_minor: int = Field(gt=0)
    currency: PaymentCurrency
    channel: PaymentChannel
    status: PaymentStatus
    created_at: datetime
    updated_at: datetime


class MockRefundRecord(BaseModel):
    """表示项目内持久化且可按动作验证的 Mock 退款结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    refund_id: str
    action_id: str
    order_id: str
    amount_minor: int = Field(gt=0)
    currency: PaymentCurrency
    channel: PaymentChannel
    status: RefundStatus
    gateway_result_code: str
    created_at: datetime
    updated_at: datetime


class RefundActionRecord(BaseModel):
    """保存服务端退款预览、审批绑定和执行状态，不代表资金结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    task_id: str
    subject_id: str
    user_id: str
    workspace_id: str
    order_id: str
    payment_id: str
    reason_code: RefundReasonCode
    reason_detail: str
    amount_minor: int = Field(gt=0)
    currency: PaymentCurrency
    channel: PaymentChannel
    policy_version: str
    policy_fact_ids: tuple[str, ...]
    facts_fingerprint: str
    preview_hash: str
    idempotency_key: str
    status: RefundActionStatus
    created_at: datetime
    decided_at: datetime | None = None
    updated_at: datetime


class RefundAuditEventRecord(BaseModel):
    """表示单个退款动作对应的脱敏、幂等审计事件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    action_id: str
    event_key: str
    event_type: str
    actor_id: str
    result_code: str
    preview_hash: str
    created_at: datetime


class LlmUsageRecord(BaseModel):
    """表示某用户在一个 UTC 日期内已接受的模型调用次数。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    usage_date: date
    accepted_calls: int = Field(ge=0)
