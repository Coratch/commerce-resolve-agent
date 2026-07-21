"""实现 L2 升级、工具白名单、预算和无进展检测的确定性规则。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from commerce_resolve.l2_models import (
    L2BudgetLimits,
    L2BudgetState,
    L2RuntimeState,
    L2ToolCall,
    L2ToolName,
)

DEFAULT_L2_TOOLS: tuple[L2ToolName, ...] = (
    "get_order",
    "get_shipment",
    "get_refund_status",
    "search_policy",
    "list_confirmed_preferences",
)


@dataclass(frozen=True)
class L2UpgradeDecision:
    """返回是否允许展示升级预览及稳定拒绝原因。"""

    allowed: bool
    reason_code: str


def decide_l2_upgrade(
    *,
    registered: bool,
    llm_allowed: bool,
    quota_remaining: int,
    sale_support_candidate: bool,
    has_conflicting_interrupt: bool,
    has_active_case: bool,
) -> L2UpgradeDecision:
    """使用可信服务端能力判断是否允许展示 L2 升级预览。"""

    if not registered:
        return L2UpgradeDecision(False, "l2_registered_required")
    if not llm_allowed:
        return L2UpgradeDecision(False, "l2_model_not_authorized")
    if quota_remaining <= 0:
        return L2UpgradeDecision(False, "l2_quota_exceeded")
    if not sale_support_candidate:
        return L2UpgradeDecision(False, "l2_out_of_scope")
    if has_conflicting_interrupt:
        return L2UpgradeDecision(False, "l2_pending_action_conflict")
    if has_active_case:
        return L2UpgradeDecision(False, "l2_case_already_active")
    return L2UpgradeDecision(True, "allowed")


def estimate_tokens(serialized_context: str, max_output_tokens: int = 800) -> int:
    """用保守字符比估算模型 Token，Provider usage 缺失时仍能限制成本。"""

    input_estimate = max(1, (len(serialized_context) + 1) // 2)
    return input_estimate + max_output_tokens


def tool_action_signature(call: L2ToolCall) -> str:
    """对规范化工具与参数生成稳定摘要，用于检测无进展重复。"""

    payload = json.dumps(
        call.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_tool_call(
    runtime: L2RuntimeState,
    call: L2ToolCall,
) -> str | None:
    """在执行前校验工具白名单、总预算和重复动作，返回拒绝原因。"""

    if call.tool not in runtime.allowed_tools:
        return "tool_not_allowed"
    if runtime.budget.tool_calls_used >= runtime.budget_limits.max_tool_calls:
        return "tool_budget_exhausted"
    signature = tool_action_signature(call)
    if (
        runtime.budget.last_action_signature == signature
        and runtime.budget.repeated_action_count >= 1
    ):
        return "no_progress"
    return None


def check_model_budget(
    runtime: L2RuntimeState,
    *,
    projected_tokens: int,
) -> str | None:
    """在调用模型前检查步骤、调用、Token 和活跃时长预算。"""

    limits = runtime.budget_limits
    used = runtime.budget
    if used.steps_used >= limits.max_steps:
        return "step_budget_exhausted"
    if used.model_calls_used >= limits.max_model_calls:
        return "model_budget_exhausted"
    if used.estimated_tokens_used + projected_tokens > limits.max_estimated_tokens:
        return "token_budget_exhausted"
    if used.active_milliseconds >= limits.max_active_milliseconds:
        return "time_budget_exhausted"
    return None


def budget_after_model_call(
    budget: L2BudgetState,
    *,
    charged_tokens: int,
    duration_ms: int,
) -> L2BudgetState:
    """返回一次模型调用完成后的不可变预算快照。"""

    return budget.model_copy(
        update={
            "steps_used": budget.steps_used + 1,
            "model_calls_used": budget.model_calls_used + 1,
            "estimated_tokens_used": budget.estimated_tokens_used
            + max(0, charged_tokens),
            "active_milliseconds": budget.active_milliseconds + max(0, duration_ms),
        }
    )


def budget_after_tool_call(
    budget: L2BudgetState,
    *,
    signature: str,
    succeeded: bool,
    duration_ms: int,
) -> L2BudgetState:
    """更新工具、失败和重复计数，不修改传入预算对象。"""

    repeated = (
        budget.repeated_action_count + 1
        if budget.last_action_signature == signature
        else 0
    )
    return budget.model_copy(
        update={
            "tool_calls_used": budget.tool_calls_used + 1,
            "active_milliseconds": budget.active_milliseconds + max(0, duration_ms),
            "consecutive_tool_failures": (
                0 if succeeded else budget.consecutive_tool_failures + 1
            ),
            "last_action_signature": signature,
            "repeated_action_count": repeated,
        }
    )


def default_budget() -> L2BudgetLimits:
    """返回 v0.5 已审核通过的默认 Harness 预算。"""

    return L2BudgetLimits()
