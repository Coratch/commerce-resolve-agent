"""定义同源内部 JSON API 的严格请求与公开响应。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from commerce_resolve.business_models import (
    MockPaymentRecord,
    MockRefundRecord,
    OrderStatus,
    PaymentChannel,
    PaymentCurrency,
    PaymentStatus,
    RefundStatus,
    ShipmentStatus,
    format_minor_units,
)
from commerce_resolve.conversation_models import (
    AgentRun,
    ConversationMessage,
    ConversationSummary,
    RunKind,
    RunStatus,
)
from commerce_resolve.l2_models import (
    CustomerPreference,
    L2CaseMetrics,
    L2CaseRecord,
    L2ContextPublicSummary,
    L2PublicTraceEvent,
    L2UpgradePreview,
    MemoryProposal,
    MemoryValue,
)
from commerce_resolve.models import (
    PolicyCitation,
    RefundPreview,
    RefundVerification,
)


class StrictRequest(BaseModel):
    """作为拒绝客户端额外身份或权限字段的请求基类。"""

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictRequest):
    """接收邀请注册所需的最小凭据。"""

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)
    invitation_code: str = Field(min_length=16, max_length=128)


class LoginRequest(StrictRequest):
    """接收账号登录凭据。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChatMessageRequest(StrictRequest):
    """接收当前 thread 和一条用户消息。"""

    thread_id: str = Field(min_length=36, max_length=36)
    message: str = Field(min_length=1, max_length=2000)


class RefundApprovalRequest(StrictRequest):
    """只允许客户端提交服务端动作标识和批准或拒绝决定。"""

    action_id: str = Field(min_length=36, max_length=36)
    decision: Literal["approve", "reject"]


class L2UpgradeDecisionRequest(StrictRequest):
    """只允许客户端确认或取消服务端已保存的升级预览。"""

    preview_id: str = Field(min_length=36, max_length=36)
    decision: Literal["confirm", "cancel"]


class L2MemoryDecisionRequest(StrictRequest):
    """只允许客户端确认或拒绝当前 Case 的受限偏好建议。"""

    proposal_id: str = Field(min_length=1, max_length=64)
    decision: Literal["confirm", "reject"]


class MemoryUpdateRequest(StrictRequest):
    """只接收目标偏好的新受限枚举值。"""

    value: MemoryValue


class SessionCapabilities(BaseModel):
    """向前端公开当前模式下可展示的确定性能力。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    can_manage_orders: bool
    can_manage_refunds: bool
    can_use_llm: bool


class SessionResponse(BaseModel):
    """返回浏览器当前访问模式和内存态 CSRF Token。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["guest", "registered"]
    username: str | None = None
    session_scope: Literal["browser", "account"]
    csrf_token: str
    expires_at: datetime
    capabilities: SessionCapabilities


class RegistrationResponse(BaseModel):
    """返回邀请注册成功后的有限公开结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    username: str
    status: Literal["registered"] = "registered"


class ConversationResponse(BaseModel):
    """返回当前身份新建的随机 conversation thread。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str


class AsyncChatMessageRequest(StrictRequest):
    """接收客户端稳定请求 ID 和一条新用户消息。"""

    client_message_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class ConversationLifecycleRequest(StrictRequest):
    """只允许在 active 与 archived 两种公开生命周期间切换。"""

    lifecycle_status: Literal["active", "archived"]


class RetryRunRequest(StrictRequest):
    """接收一次显式重试使用的新客户端请求 ID。"""

    client_message_id: str = Field(min_length=8, max_length=64)


class PublicAgentRun(BaseModel):
    """向当前会话所有者公开 Run 状态，不披露请求摘要和 Checkpoint 标识。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    thread_id: str
    client_request_id: str
    request_kind: RunKind
    retry_of_run_id: str | None = None
    status: RunStatus
    pending_action: str | None = None
    public_error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime

    @classmethod
    def from_domain(cls, run: AgentRun) -> PublicAgentRun:
        """从内部 Run 投影前端恢复所需的有限公开字段。"""

        return cls(
            run_id=run.run_id,
            thread_id=run.thread_id,
            client_request_id=run.client_request_id,
            request_kind=run.request_kind,
            retry_of_run_id=run.retry_of_run_id,
            status=run.status,
            pending_action=run.pending_action,
            public_error_code=run.public_error_code,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            updated_at=run.updated_at,
        )


class ConversationListResponse(BaseModel):
    """返回当前身份可见的会话列表和下一页游标。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversations: tuple[ConversationSummary, ...]
    next_cursor: str | None = None


class ConversationDetailResponse(BaseModel):
    """返回会话摘要及当前公开待处理动作。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation: ConversationSummary


class ConversationMessagesResponse(BaseModel):
    """返回按序号递增的公开消息页和历史完整性状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[ConversationMessage, ...]
    history_state: Literal["complete", "partial"]
    next_after_sequence: int | None = None


class RunAcceptedResponse(BaseModel):
    """返回已持久接受或幂等复用的 Agent Run 资源。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: PublicAgentRun
    user_message: ConversationMessage
    reused: bool


class AgentRunResponse(BaseModel):
    """返回授权会话中的当前 Run 状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: PublicAgentRun


class PublicL2UpgradePreview(BaseModel):
    """向浏览器披露 AI 身份、工具类别和固定预算的升级预览。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    preview_id: str
    issue_summary: str
    related_order_id: str | None = None
    context_categories: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    max_steps: int
    reads_confirmed_preferences: bool
    agent_identity: Literal["AI 二线客服，并非真人"] = "AI 二线客服，并非真人"

    @classmethod
    def from_domain(cls, preview: L2UpgradePreview) -> PublicL2UpgradePreview:
        """移除内部哈希后构造可确认的公开升级卡片。"""

        return cls(
            preview_id=preview.preview_id,
            issue_summary=preview.issue_summary,
            related_order_id=preview.related_order_id,
            context_categories=preview.context_categories,
            allowed_tools=preview.allowed_tools,
            max_steps=preview.budget.max_steps,
            reads_confirmed_preferences=preview.reads_confirmed_preferences,
        )


class PublicMemoryProposal(BaseModel):
    """展示待确认偏好具体内容，不允许客户端修改建议。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    memory_type: str
    value: str
    purpose: str

    @classmethod
    def from_domain(cls, proposal: MemoryProposal) -> PublicMemoryProposal:
        """把内部 Case 绑定建议转换为有限公开字段。"""

        return cls(
            proposal_id=proposal.proposal_id,
            memory_type=proposal.memory_type,
            value=proposal.value,
            purpose=proposal.purpose,
        )


class PublicL2TraceEvent(BaseModel):
    """向用户公开不含 Prompt 和隐藏推理的 L2 动作轨迹。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence_no: int
    payload_version: int
    step_number: int
    event_type: str
    tool_category: str | None = None
    risk: str | None = None
    parameter_summary: dict[str, str] | None = None
    result_code: str
    evidence_ids: tuple[str, ...]
    duration_ms: int
    context_summary: L2ContextPublicSummary | None = None
    created_at: datetime

    @classmethod
    def from_domain(cls, event: L2PublicTraceEvent) -> PublicL2TraceEvent:
        """复制经领域 Schema 脱敏的公开轨迹字段。"""

        return cls(
            sequence_no=event.sequence_no,
            payload_version=event.payload_version,
            step_number=event.step_number,
            event_type=event.event_type,
            tool_category=event.tool_category,
            risk=event.risk,
            parameter_summary=event.parameter_summary,
            result_code=event.result_code,
            evidence_ids=event.evidence_ids,
            duration_ms=event.duration_ms,
            context_summary=event.context_summary,
            created_at=event.created_at,
        )


class PublicL2CaseSummary(BaseModel):
    """返回本人 L2 Case 的公开状态、预算和最终结果摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    thread_id: str
    related_order_id: str | None = None
    issue_summary: str
    status: str
    stop_reason: str | None = None
    trace_state: str
    context_policy_version: str | None = None
    failure_attribution: str | None = None
    steps_used: int
    model_calls_used: int
    tool_calls_used: int
    final_response: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, case: L2CaseRecord) -> PublicL2CaseSummary:
        """移除身份、模型和内部预算配置后构造 Case 摘要。"""

        return cls(
            case_id=case.case_id,
            thread_id=case.thread_id,
            related_order_id=case.related_order_id,
            issue_summary=case.issue_summary,
            status=case.status,
            stop_reason=case.stop_reason,
            trace_state=case.trace_state,
            context_policy_version=case.context_policy_version,
            failure_attribution=case.failure_attribution,
            steps_used=case.usage.steps_used,
            model_calls_used=case.usage.model_calls_used,
            tool_calls_used=case.usage.tool_calls_used,
            final_response=case.final_response,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )


class PublicL2CaseMetrics(BaseModel):
    """返回理解 Case 资源和上下文质量所需的有限聚合指标。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: int
    model_calls: int
    tool_calls: int
    candidate_count: int
    selected_count: int
    duplicate_count: int
    stale_count: int
    conflict_count: int
    truncated_count: int
    candidate_estimated_tokens: int
    selected_estimated_tokens: int
    provider_input_tokens: int
    provider_output_tokens: int
    usage_sources: tuple[str, ...]
    context_duration_ms: int
    model_duration_ms: int
    tool_duration_ms: int
    case_duration_ms: int

    @classmethod
    def from_domain(cls, metrics: L2CaseMetrics) -> PublicL2CaseMetrics:
        """从领域聚合中只复制允许向当前用户公开的指标字段。"""

        return cls(
            steps=metrics.steps,
            model_calls=metrics.model_calls,
            tool_calls=metrics.tool_calls,
            candidate_count=metrics.candidate_count,
            selected_count=metrics.selected_count,
            duplicate_count=metrics.duplicate_count,
            stale_count=metrics.stale_count,
            conflict_count=metrics.conflict_count,
            truncated_count=metrics.truncated_count,
            candidate_estimated_tokens=metrics.candidate_estimated_tokens,
            selected_estimated_tokens=metrics.selected_estimated_tokens,
            provider_input_tokens=metrics.provider_input_tokens,
            provider_output_tokens=metrics.provider_output_tokens,
            usage_sources=metrics.usage_sources,
            context_duration_ms=metrics.context_duration_ms,
            model_duration_ms=metrics.model_duration_ms,
            tool_duration_ms=metrics.tool_duration_ms,
            case_duration_ms=metrics.case_duration_ms,
        )


class PublicL2CaseDetail(BaseModel):
    """返回授权 Case 摘要和有界公开轨迹。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case: PublicL2CaseSummary
    events: tuple[PublicL2TraceEvent, ...]
    metrics: PublicL2CaseMetrics
    next_after_sequence: int | None = None
    has_more: bool = False


class PublicL2TracePage(BaseModel):
    """返回本人 Case 的稳定 keyset 公开 Trace 页面。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    trace_state: str
    events: tuple[PublicL2TraceEvent, ...]
    next_after_sequence: int | None = None
    has_more: bool


class L2CasesResponse(BaseModel):
    """返回当前账号最近的 L2 Case 列表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cases: tuple[PublicL2CaseSummary, ...]


class PublicCustomerPreference(BaseModel):
    """返回用户可以查看、纠正和删除的单条确认偏好。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    memory_type: str
    value: str
    source_case_id: str
    created_at: datetime
    last_confirmed_at: datetime

    @classmethod
    def from_domain(cls, item: CustomerPreference) -> PublicCustomerPreference:
        """把 Store 领域模型转换为公开偏好。"""

        return cls(
            memory_id=item.memory_id,
            memory_type=item.memory_type,
            value=item.value,
            source_case_id=item.source_case_id,
            created_at=item.created_at,
            last_confirmed_at=item.last_confirmed_at,
        )


class MemoriesResponse(BaseModel):
    """返回当前账号三类受限长期偏好。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memories: tuple[PublicCustomerPreference, ...]


class ChatResponse(BaseModel):
    """返回一轮 Graph 的公开消息、状态和政策引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str
    assistant_message: str
    public_status: str
    citations: tuple[PolicyCitation, ...] = ()
    refund_preview: PublicRefundPreview | None = None
    refund_result: PublicRefundResult | None = None
    l2_upgrade_preview: PublicL2UpgradePreview | None = None
    l2_case_summary: PublicL2CaseSummary | None = None
    l2_pending_action: (
        Literal[
            "upgrade_confirmation",
            "user_input",
            "memory_confirmation",
            "refund_approval",
        ]
        | None
    ) = None
    l2_trace_events: tuple[PublicL2TraceEvent, ...] = ()
    memory_proposal: PublicMemoryProposal | None = None


class PendingL2Response(BaseModel):
    """返回 conversation 当前是否存在 L2 结构化待处理动作。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: bool
    public_status: str
    pending_action: str | None = None
    upgrade_preview: PublicL2UpgradePreview | None = None
    memory_proposal: PublicMemoryProposal | None = None


class PublicRefundPreview(BaseModel):
    """向浏览器展示服务端绑定的 Mock 退款审批内容。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    order_id: str
    reason_code: str
    reason_detail: str
    amount: str
    currency: PaymentCurrency
    channel: PaymentChannel
    order_status: OrderStatus
    shipment_status: ShipmentStatus | None = None
    payment_status: Literal["settled"]
    risk: Literal["R2"]
    citations: tuple[PolicyCitation, ...]

    @classmethod
    def from_domain(cls, preview: RefundPreview) -> PublicRefundPreview:
        """移除 task、指纹和哈希后构造可审批公开预览。"""

        return cls(
            action_id=preview.action_id,
            order_id=preview.order_id,
            reason_code=preview.reason.code,
            reason_detail=preview.reason.detail,
            amount=preview.display_amount,
            currency=preview.currency,
            channel=preview.channel,
            order_status=preview.order_status,
            shipment_status=preview.shipment_status,
            payment_status=preview.payment_status,
            risk=preview.risk,
            citations=preview.citations,
        )


class PublicRefundResult(BaseModel):
    """向浏览器返回经过业务回读验证的 Mock 退款结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    refund_id: str | None = None
    amount: str
    status: str | None = None
    verified: bool
    result_code: str

    @classmethod
    def from_domain(cls, result: RefundVerification) -> PublicRefundResult:
        """把整数分和内部验证记录转换为有限公开结果。"""

        return cls(
            action_id=result.action_id,
            refund_id=result.refund_id,
            amount=format_minor_units(result.amount_minor),
            status=result.status,
            verified=result.verified,
            result_code=result.result_code,
        )


class PendingRefundResponse(BaseModel):
    """返回当前 conversation 是否存在可决定的待审批退款。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: bool
    public_status: str
    refund_preview: PublicRefundPreview | None = None


class PublicShipment(BaseModel):
    """向浏览器返回不含内部主键和工作区信息的物流数据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ShipmentStatus
    last_event: str
    estimated_delivery_at: date | None = None


class PublicPayment(BaseModel):
    """向浏览器返回可展示但不可作为退款执行参数的 Mock 支付事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_id: str
    amount: str
    currency: PaymentCurrency
    channel: PaymentChannel
    status: PaymentStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: MockPaymentRecord) -> PublicPayment:
        """把内部整数金额转换为固定两位小数的公开支付响应。"""

        return cls(
            payment_id=record.payment_id,
            amount=format_minor_units(record.amount_minor),
            currency=record.currency,
            channel=record.channel,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class PublicRefund(BaseModel):
    """向浏览器返回已持久化 Mock 退款的只读验证摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    refund_id: str
    action_id: str
    amount: str
    currency: PaymentCurrency
    channel: PaymentChannel
    status: RefundStatus
    result_code: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: MockRefundRecord) -> PublicRefund:
        """把业务退款记录转换为不含内部作用域和幂等键的响应。"""

        return cls(
            refund_id=record.refund_id,
            action_id=record.action_id,
            amount=format_minor_units(record.amount_minor),
            currency=record.currency,
            channel=record.channel,
            status=record.status,
            result_code=record.gateway_result_code,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class PublicOrder(BaseModel):
    """向浏览器返回当前账号有权查看的订单、物流和 Mock 交易摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    status: OrderStatus
    shipment: PublicShipment | None = None
    payment: PublicPayment | None = None
    refunds: tuple[PublicRefund, ...] = ()
    created_at: datetime
    updated_at: datetime


class OrdersResponse(BaseModel):
    """返回当前私有工作区订单列表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orders: tuple[PublicOrder, ...]


class RefundsResponse(BaseModel):
    """返回指定私有订单的全部 Mock 退款只读摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    refunds: tuple[PublicRefund, ...]


class DeleteResponse(BaseModel):
    """返回确定性删除操作的公开结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: bool


class ErrorResponse(BaseModel):
    """定义不暴露内部实现的统一 Web 错误结构。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_code: str
    message: str


ChatResponse.model_rebuild()
