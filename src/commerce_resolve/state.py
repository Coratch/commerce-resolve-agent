"""订单与政策查询工作流的 LangGraph State 和单次运行 Context。"""

import operator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from langgraph.graph import MessagesState

from commerce_resolve.access import AccessMode
from commerce_resolve.l2_models import L2RuntimeState, L2UpgradePreview
from commerce_resolve.models import (
    Intent,
    OrderView,
    PolicyCitation,
    PolicyConflict,
    PolicyDimension,
    PolicyEvidenceRef,
    PolicyQuery,
    RefundContext,
    RefundEligibility,
    RefundExecutionResult,
    RefundPreview,
    RefundReason,
    RefundVerification,
    ServiceConcern,
    ShipmentView,
)
from commerce_resolve.service_resolution import ServiceResolution


class AgentState(MessagesState, total=False):
    """保存售后意图澄清、业务事实、任务状态和审计轨迹。"""

    owner_user_id: str
    owner_workspace_id: str
    intent: Intent
    intent_clarification_attempts: int
    order_id: str | None
    status: Literal[
        "awaiting_order_id",
        "awaiting_intent_clarification",
        "intent_unresolved",
        "completed",
        "order_unavailable",
        "temporarily_failed",
        "unsupported",
        "policy_answered",
        "awaiting_policy_context",
        "policy_insufficient_evidence",
        "policy_conflict",
        "service_guidance_completed",
        "service_guidance_needs_input",
        "service_guidance_incomplete",
        "awaiting_refund_context",
        "refund_ineligible",
        "refund_awaiting_approval",
        "refund_rejected",
        "refund_preview_stale",
        "refund_conflict",
        "refund_completed",
        "refund_failed",
        "refund_result_unknown",
        "refund_verification_failed",
        "l2_awaiting_confirmation",
        "l2_cancelled",
        "l2_active",
        "l2_waiting_user",
        "l2_waiting_memory_confirmation",
        "l2_resolved",
        "l2_unresolved",
        "l2_budget_exhausted",
        "l2_stopped",
    ]
    order: OrderView | None
    shipment: ShipmentView | None
    policy_query: PolicyQuery | None
    service_concerns: tuple[ServiceConcern, ...]
    service_goal_summary: str | None
    service_resolution: ServiceResolution | None
    guidance_policy_claims: tuple[str, ...]
    pending_policy_query: PolicyQuery | None
    policy_evidence_refs: tuple[PolicyEvidenceRef, ...]
    selected_policy_fact_ids: tuple[str, ...]
    policy_citations: tuple[PolicyCitation, ...]
    policy_conflicts: tuple[PolicyConflict, ...]
    missing_policy_dimensions: tuple[PolicyDimension, ...]
    policy_index_version: str | None
    pending_refund_request: bool
    refund_reason: RefundReason | None
    refund_context: RefundContext | None
    refund_eligibility: RefundEligibility | None
    refund_action_id: str | None
    refund_preview: RefundPreview | None
    refund_policy_fact_ids: tuple[str, ...]
    refund_result: RefundExecutionResult | None
    refund_verification: RefundVerification | None
    l2_upgrade_preview: L2UpgradePreview | None
    l2_runtime: L2RuntimeState | None
    error_code: str | None
    audit: Annotated[list[str], operator.add]


@dataclass(frozen=True)
class RunContext:
    """携带单次调用使用且不自动持久化的可信业务作用域。"""

    user_id: str
    workspace_id: str = "cli-demo"
    access_mode: AccessMode = "cli"
    as_of: date | None = None
    task_id: str | None = None
    subject_id: str | None = None
    l2_allowed: bool = False
    l2_quota_remaining: int = 0
    bound_order_id: str | None = None
