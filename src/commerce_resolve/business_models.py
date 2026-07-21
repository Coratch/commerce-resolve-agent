"""定义账号、会话、工作区及私有业务数据的领域模型。"""

import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

UserStatus = Literal["active", "disabled"]
WebActorType = Literal["guest", "registered"]
OrderStatus = Literal["processing", "shipped", "delivered", "cancelled"]
ShipmentStatus = Literal["preparing", "in_transit", "delivered"]
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
    created_at: datetime


class Workspace(BaseModel):
    """表示一个注册用户独占的私有演示工作区。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    owner_user_id: str
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
    expires_at: datetime


class ConversationRecord(BaseModel):
    """保存 conversation 与服务端身份、工作区的绑定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str
    subject_id: str
    workspace_id: str
    access_mode: WebActorType
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


class OrderCreate(BaseModel):
    """校验私有工作区中新建订单及可选物流。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")
    status: OrderStatus
    shipment: ShipmentInput | None = None


class OrderUpdate(BaseModel):
    """校验订单与物流的部分更新，至少要求一个字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: OrderStatus | None = None
    shipment: ShipmentInput | None = None
    remove_shipment: bool = False

    @model_validator(mode="after")
    def validate_has_change(self) -> Self:
        """拒绝没有任何状态或物流变更的空更新。"""

        if self.status is None and self.shipment is None and not self.remove_shipment:
            raise ValueError("order update requires at least one change")
        if self.shipment is not None and self.remove_shipment:
            raise ValueError("shipment and remove_shipment cannot be combined")
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
