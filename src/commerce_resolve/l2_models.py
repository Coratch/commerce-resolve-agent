"""定义 L2 Agent Harness、公开 Case 轨迹与长期偏好的强类型模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from commerce_resolve.models import PolicyQuery, RefundReason

L2CaseStatus = Literal[
    "l2_active",
    "l2_waiting_user",
    "l2_waiting_approval",
    "l2_resolved",
    "l2_unresolved",
    "l2_budget_exhausted",
    "l2_cancelled",
    "l2_stopped",
]
L2RuntimePhase = Literal[
    "awaiting_confirmation",
    "active",
    "waiting_user",
    "waiting_memory_confirmation",
    "waiting_refund_approval",
    "resolved",
    "unresolved",
    "budget_exhausted",
    "cancelled",
    "stopped",
]
L2StopReason = Literal[
    "resolved",
    "user_cancelled",
    "unsupported",
    "insufficient_evidence",
    "safety_rejected",
    "model_unavailable",
    "invalid_model_output",
    "tool_failed",
    "no_progress",
    "budget_exhausted",
    "identity_changed",
    "state_conflict",
]
L2ToolName = Literal[
    "get_order",
    "get_shipment",
    "get_refund_status",
    "search_policy",
    "list_confirmed_preferences",
]
L2FailureAttribution = Literal[
    "context_missing",
    "context_stale",
    "context_conflict",
    "user_input_required",
    "model_unavailable",
    "model_output_invalid",
    "tool_rejected",
    "tool_failed",
    "policy_blocked",
    "budget_exhausted",
    "verification_failed",
]
L2ContextSourceType = Literal[
    "control",
    "case_goal",
    "public_message",
    "business_observation",
    "policy_observation",
    "confirmed_preference",
]
L2ContextDisposition = Literal[
    "selected",
    "duplicate",
    "irrelevant",
    "stale",
    "conflict",
    "out_of_scope",
    "truncated",
]
L2ContextFreshness = Literal["fresh", "stale", "unknown", "not_applicable"]
L2TraceState = Literal["complete", "partial", "unavailable"]
L2UsageSource = Literal["provider", "estimated", "unknown"]
MemoryType = Literal[
    "preferred_language",
    "response_detail",
    "communication_tone",
]
MemoryValue = Literal[
    "zh-CN",
    "en",
    "concise",
    "standard",
    "detailed",
    "neutral",
    "friendly",
]


class L2BudgetLimits(BaseModel):
    """保存 Case 创建时固定且不可由模型扩大的循环预算。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(default=8, ge=1, le=20)
    max_model_calls: int = Field(default=6, ge=1, le=12)
    max_tool_calls: int = Field(default=8, ge=1, le=20)
    max_estimated_tokens: int = Field(default=30_000, ge=1_000, le=100_000)
    max_active_milliseconds: int = Field(default=120_000, ge=1_000, le=600_000)
    max_invocation_milliseconds: int = Field(
        default=45_000,
        ge=1_000,
        le=120_000,
    )
    max_consecutive_tool_failures: int = Field(default=2, ge=1, le=5)


class L2BudgetState(BaseModel):
    """记录可持久恢复的已用预算与无进展检测状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps_used: int = Field(default=0, ge=0)
    model_calls_used: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    estimated_tokens_used: int = Field(default=0, ge=0)
    active_milliseconds: int = Field(default=0, ge=0)
    consecutive_tool_failures: int = Field(default=0, ge=0)
    last_action_signature: str | None = Field(default=None, max_length=64)
    repeated_action_count: int = Field(default=0, ge=0)


class L2UpgradePreview(BaseModel):
    """保存用户确认前可公开且绑定服务端上下文的升级预览。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    preview_id: str = Field(min_length=1, max_length=64)
    issue_summary: str = Field(min_length=1, max_length=500)
    related_order_id: str | None = Field(default=None, max_length=36)
    context_categories: tuple[str, ...] = Field(max_length=8)
    allowed_tools: tuple[L2ToolName, ...] = Field(min_length=1, max_length=5)
    budget: L2BudgetLimits
    reads_confirmed_preferences: bool = True
    preview_hash: str = Field(min_length=64, max_length=64)


class GetOrderCall(BaseModel):
    """请求读取当前账号可访问的单个订单。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Literal["get_order"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")


class GetShipmentCall(BaseModel):
    """请求读取当前账号订单对应的物流信息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Literal["get_shipment"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")


class GetRefundStatusCall(BaseModel):
    """请求读取当前账号订单的 Mock 退款状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Literal["get_refund_status"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")


class SearchPolicyCall(BaseModel):
    """请求使用既有受限 PolicyQuery 检索已发布售后政策。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Literal["search_policy"]
    query_text: str = Field(min_length=1, max_length=300)
    query: PolicyQuery


class ListConfirmedPreferencesCall(BaseModel):
    """请求读取当前账号已经确认的三类低风险偏好。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Literal["list_confirmed_preferences"]


type L2ToolCall = Annotated[
    GetOrderCall
    | GetShipmentCall
    | GetRefundStatusCall
    | SearchPolicyCall
    | ListConfirmedPreferencesCall,
    Field(discriminator="tool"),
]


class ToolCallDecision(BaseModel):
    """表示模型提出一个仍需 Harness 校验的 R0 工具调用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool_call"]
    call: L2ToolCall


class AskUserDecision(BaseModel):
    """表示模型缺少必要信息并请求暂停等待用户补充。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ask_user"]
    question: str = Field(min_length=1, max_length=300)
    expected_field: Literal[
        "order_id",
        "refund_reason",
        "product_context",
        "clarification",
    ]


class ProposeRefundDecision(BaseModel):
    """表示模型建议进入既有退款流程，但不包含批准或金额。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["propose_refund"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")
    reason: RefundReason


class ProposeMemoryDecision(BaseModel):
    """表示模型建议保存一条仍待用户确认的受限偏好。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["propose_memory"]
    memory_type: MemoryType
    value: MemoryValue
    purpose: Literal[
        "后续客服使用该语言回复",
        "后续客服采用该回复详细程度",
        "后续客服采用该沟通语气",
    ]

    @model_validator(mode="after")
    def validate_value_matches_type(self) -> ProposeMemoryDecision:
        """拒绝把一种偏好的枚举值保存到另一种偏好类型。"""

        allowed = {
            "preferred_language": {"zh-CN", "en"},
            "response_detail": {"concise", "standard", "detailed"},
            "communication_tone": {"neutral", "friendly"},
        }
        if self.value not in allowed[self.memory_type]:
            raise ValueError("memory value does not match memory type")
        return self


class AnswerDecision(BaseModel):
    """表示模型基于已有 Observation 提出可验证的最终回答。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["answer"]
    answer: str = Field(min_length=1, max_length=1200)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)


class StopDecision(BaseModel):
    """表示模型明确无法继续并提供安全公开说明。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["stop"]
    reason: Literal[
        "unsupported",
        "insufficient_evidence",
        "safety_rejected",
        "model_limit",
    ]
    public_message: str = Field(min_length=1, max_length=600)


type L2Decision = Annotated[
    ToolCallDecision
    | AskUserDecision
    | ProposeRefundDecision
    | ProposeMemoryDecision
    | AnswerDecision
    | StopDecision,
    Field(discriminator="kind"),
]


class L2ModelUsage(BaseModel):
    """保存 Provider 返回或 Harness 估算的最小模型用量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        """返回输入和输出 Token 的非负总和。"""

        return self.input_tokens + self.output_tokens


class L2ModelResult(BaseModel):
    """返回已经通过 Schema 校验的单步模型决策与用量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: L2Decision
    usage: L2ModelUsage


class OrderObservationSource(BaseModel):
    """记录订单 Observation 可重新验证的来源标识和版本。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["order"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")
    source_version: str = Field(min_length=64, max_length=64)


class ShipmentObservationSource(BaseModel):
    """记录物流 Observation 可重新验证的来源标识和版本。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["shipment"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")
    source_version: str = Field(min_length=64, max_length=64)


class RefundObservationSource(BaseModel):
    """记录退款 Observation 可重新验证的来源标识和版本。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["refund"]
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]{3,32}$")
    source_version: str = Field(min_length=64, max_length=64)


class PolicyObservationFact(BaseModel):
    """保存政策事实的稳定引用及冲突判断所需的规范化值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str = Field(min_length=1, max_length=120)
    content_hash: str = Field(min_length=64, max_length=64)
    rule_key: str = Field(min_length=1, max_length=120)
    normalized_value: str = Field(min_length=1, max_length=240)


class PolicyObservationSource(BaseModel):
    """记录政策语料版本和被解析事实，支持调用前重新验证。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["policy"]
    corpus_version: str = Field(min_length=1, max_length=80)
    corpus_hash: str = Field(min_length=1, max_length=64)
    facts: tuple[PolicyObservationFact, ...] = Field(default=(), max_length=16)


class PreferenceObservationSource(BaseModel):
    """记录受限偏好工具结果的当前来源版本和 Memory 引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["preference"]
    source_version: str = Field(min_length=64, max_length=64)
    memory_ids: tuple[str, ...] = Field(default=(), max_length=3)


type L2ObservationSource = Annotated[
    OrderObservationSource
    | ShipmentObservationSource
    | RefundObservationSource
    | PolicyObservationSource
    | PreferenceObservationSource,
    Field(discriminator="kind"),
]


class L2Observation(BaseModel):
    """保存经过 Tool Harness 校验、脱敏和截断的单步观察。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    source_type: str = Field(min_length=1, max_length=48)
    source_ref: str = Field(min_length=1, max_length=160)
    result_code: str = Field(min_length=1, max_length=80)
    summary: str = Field(max_length=3000)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)
    observed_at: datetime
    source_metadata: L2ObservationSource | None = None


class MemoryProposal(BaseModel):
    """保存待用户确认且绑定当前 Case 的长期偏好建议。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    memory_type: MemoryType
    value: MemoryValue
    purpose: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_value_matches_type(self) -> MemoryProposal:
        """复用固定枚举映射，拒绝自由文本或跨类型偏好。"""

        ProposeMemoryDecision(
            kind="propose_memory",
            memory_type=self.memory_type,
            value=self.value,
            purpose=self.purpose,
        )
        return self


class CustomerPreference(BaseModel):
    """表示用户明确确认并可独立管理的一条长期偏好。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)
    memory_type: MemoryType
    value: MemoryValue
    source_case_id: str = Field(min_length=1, max_length=64)
    schema_version: Literal[1] = 1
    created_at: datetime
    last_confirmed_at: datetime

    @model_validator(mode="after")
    def validate_value_matches_type(self) -> CustomerPreference:
        """确保持久化值仍满足当前版本的受限偏好 Schema。"""

        allowed = {
            "preferred_language": {"zh-CN", "en"},
            "response_detail": {"concise", "standard", "detailed"},
            "communication_tone": {"neutral", "friendly"},
        }
        if self.value not in allowed[self.memory_type]:
            raise ValueError("memory value does not match memory type")
        return self


class L2ContextPublicMessage(BaseModel):
    """表示经过作用域校验且允许进入 Context Pack 的公开消息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(min_length=1, max_length=64)
    sequence_no: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class L2ContextPublicSummary(BaseModel):
    """表示可以投影给用户的有限上下文来源和处理结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_types: tuple[L2ContextSourceType, ...] = Field(default=(), max_length=6)
    selected_count: int = Field(ge=0)
    public_evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)
    truncated: bool = False
    facts_refreshed: int = Field(default=0, ge=0)
    state_changed: bool = False
    essential_complete: bool = True


class L2ContextManifestItem(BaseModel):
    """保存单个候选的脱敏来源、版本、选择结果和稳定原因码。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=96)
    source_type: L2ContextSourceType
    source_ref: str = Field(min_length=1, max_length=240)
    source_version: str | None = Field(default=None, max_length=160)
    freshness: L2ContextFreshness
    disposition: L2ContextDisposition
    reason_code: str = Field(min_length=1, max_length=80)
    essential: bool = False
    estimated_input_tokens: int = Field(ge=0)


class L2ContextPack(BaseModel):
    """定义一次模型决策实际可见且受预算限制的结构化上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_summary: str = Field(min_length=1, max_length=500)
    latest_user_input: str | None = Field(default=None, max_length=2000)
    related_order_id: str | None = Field(default=None, max_length=36)
    public_messages: tuple[L2ContextPublicMessage, ...] = Field(
        default=(), max_length=12
    )
    observations: tuple[L2Observation, ...] = Field(default=(), max_length=12)
    confirmed_preferences: tuple[CustomerPreference, ...] = Field(
        default=(), max_length=3
    )
    allowed_tools: tuple[L2ToolName, ...] = Field(min_length=1, max_length=5)
    remaining_steps: int = Field(ge=0)
    remaining_model_calls: int = Field(ge=0)
    remaining_tool_calls: int = Field(ge=0)
    remaining_estimated_tokens: int = Field(ge=0)
    change_notes: tuple[str, ...] = Field(default=(), max_length=8)


class L2ContextManifest(BaseModel):
    """保存一次 Context 选择的元数据，不复制实际消息或工具正文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str = Field(min_length=1, max_length=64)
    schema_version: Literal[1] = 1
    case_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    context_policy_version: str = Field(min_length=1, max_length=40)
    scope_fingerprint: str = Field(min_length=64, max_length=64)
    pack_hash: str = Field(min_length=64, max_length=64)
    essential_complete: bool
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    irrelevant_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    out_of_scope_count: int = Field(ge=0)
    truncated_count: int = Field(ge=0)
    refresh_count: int = Field(ge=0)
    candidate_estimated_tokens: int = Field(ge=0)
    selected_estimated_tokens: int = Field(ge=0)
    pack_estimated_input_tokens: int = Field(ge=0)
    input_budget_tokens: int = Field(ge=0)
    reduction_basis_points: int = Field(ge=0, le=10_000)
    truncated: bool
    failure_attribution: L2FailureAttribution | None = None
    public_summary: L2ContextPublicSummary
    items: tuple[L2ContextManifestItem, ...] = Field(default=(), max_length=140)
    context_preparation_ms: int = Field(default=0, ge=0, le=600_000)
    created_at: datetime


class L2ObservationRefreshResult(BaseModel):
    """表示只读来源校验后的 fresh Observation 或稳定失败状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    freshness: L2ContextFreshness
    observation: L2Observation | None = None
    changed: bool = False
    result_code: str = Field(min_length=1, max_length=80)


class L2RuntimeState(BaseModel):
    """保存当前 L2 Case 可恢复的有界工作记忆和控制状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    preview_id: str | None = Field(default=None, max_length=64)
    case_id: str | None = Field(default=None, max_length=64)
    phase: L2RuntimePhase
    issue_summary: str = Field(min_length=1, max_length=500)
    related_order_id: str | None = Field(default=None, max_length=36)
    allowed_tools: tuple[L2ToolName, ...] = Field(default=(), max_length=5)
    observations: tuple[L2Observation, ...] = Field(default=(), max_length=20)
    pending_decision: L2Decision | None = None
    pending_memory_proposal: MemoryProposal | None = None
    budget_limits: L2BudgetLimits = Field(default_factory=L2BudgetLimits)
    budget: L2BudgetState = Field(default_factory=L2BudgetState)
    latest_user_input: str | None = Field(default=None, max_length=2000)
    final_response: str | None = Field(default=None, max_length=1200)
    stop_reason: L2StopReason | None = None
    context_policy_version: str | None = Field(default=None, max_length=40)
    last_context_manifest_id: str | None = Field(default=None, max_length=64)
    last_context_evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)
    failure_attribution: L2FailureAttribution | None = None


class L2CaseCreate(BaseModel):
    """定义用户确认后幂等创建 L2 Case 所需的可信字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=64)
    thread_id: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    related_order_id: str | None = Field(default=None, max_length=36)
    issue_summary: str = Field(min_length=1, max_length=500)
    model_name: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=40)
    toolset_version: str = Field(min_length=1, max_length=40)
    context_policy_version: str | None = Field(default=None, max_length=40)
    budget: L2BudgetLimits


class L2CaseRecord(BaseModel):
    """表示业务 Repository 中可授权查询的 L2 Case 公开事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    thread_id: str
    subject_id: str
    user_id: str
    workspace_id: str
    related_order_id: str | None = None
    issue_summary: str
    status: L2CaseStatus
    stop_reason: L2StopReason | None = None
    model_name: str
    prompt_version: str
    toolset_version: str
    context_policy_version: str | None = None
    trace_state: L2TraceState = "partial"
    failure_attribution: L2FailureAttribution | None = None
    budget: L2BudgetLimits
    usage: L2BudgetState
    final_response: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class L2CaseTransition(BaseModel):
    """定义幂等状态迁移及同步持久化的预算和公开结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_statuses: tuple[L2CaseStatus, ...] = Field(min_length=1)
    status: L2CaseStatus
    stop_reason: L2StopReason | None = None
    usage: L2BudgetState
    final_response: str | None = Field(default=None, max_length=1200)
    failure_attribution: L2FailureAttribution | None = None


class L2PublicTraceEvent(BaseModel):
    """表示可向用户和 Eval 暴露的脱敏 Agent Harness 事件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    event_key: str = Field(min_length=1, max_length=120)
    sequence_no: int = Field(default=0, ge=0)
    payload_version: int = Field(default=1, ge=1)
    step_number: int = Field(ge=0, le=20)
    event_type: str = Field(min_length=1, max_length=48)
    tool_category: L2ToolName | None = None
    risk: Literal["R0", "R1", "R2"] | None = None
    parameter_summary: dict[str, str] | None = None
    result_code: str = Field(min_length=1, max_length=80)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)
    duration_ms: int = Field(default=0, ge=0, le=600_000)
    context_summary: L2ContextPublicSummary | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_parameter_summary(self) -> L2PublicTraceEvent:
        """限制公开参数摘要数量和长度，避免把完整工具正文写入审计。"""

        if self.parameter_summary is None:
            return self
        if len(self.parameter_summary) > 8:
            raise ValueError("trace parameter summary has too many fields")
        for key, value in self.parameter_summary.items():
            if not key or len(key) > 40 or len(value) > 160:
                raise ValueError("trace parameter summary is too large")
        return self


class L2ModelCallStart(BaseModel):
    """定义一次真实 L2 Provider 尝试的原子计量输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    thread_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=120)
    manifest_id: str | None = Field(default=None, max_length=64)
    charged_tokens: int = Field(ge=0, le=100_000)
    created_at: datetime


class L2ModelCallRecord(BaseModel):
    """表示业务数据库中的一条 L2 模型计量记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    user_id: str
    thread_id: str
    case_id: str
    step_id: str
    model_name: str
    manifest_id: str | None = None
    status: Literal["started", "completed", "failed"]
    input_tokens: int
    output_tokens: int
    charged_tokens: int
    duration_ms: int
    usage_source: L2UsageSource = "unknown"
    created_at: datetime
    completed_at: datetime | None = None


class L2CaseMetrics(BaseModel):
    """聚合公开 Case Trace、上下文、Token、耗时和预算指标。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    user_questions: int = Field(ge=0)
    approvals: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    truncated_count: int = Field(ge=0)
    candidate_estimated_tokens: int = Field(ge=0)
    selected_estimated_tokens: int = Field(ge=0)
    provider_input_tokens: int = Field(ge=0)
    provider_output_tokens: int = Field(ge=0)
    usage_sources: tuple[L2UsageSource, ...] = Field(default=(), max_length=3)
    context_duration_ms: int = Field(ge=0)
    model_duration_ms: int = Field(ge=0)
    tool_duration_ms: int = Field(ge=0)
    case_duration_ms: int = Field(ge=0)
    budget_limits: L2BudgetLimits
    budget_used: L2BudgetState
    status: L2CaseStatus
    stop_reason: L2StopReason | None = None
    failure_attribution: L2FailureAttribution | None = None


class L2ModelRequest(BaseModel):
    """定义传给 L2 Model Adapter 的最小、受限公开上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    step_id: str
    context_policy_version: str = Field(min_length=1, max_length=40)
    context: L2ContextPack
