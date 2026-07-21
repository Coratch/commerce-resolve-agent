"""提供 L2 失败归因和公开提示的确定性规则。"""

from __future__ import annotations

from commerce_resolve.l2_models import L2FailureAttribution

_PUBLIC_FAILURE_MESSAGES: dict[L2FailureAttribution, str] = {
    "context_missing": "当前处理所需的信息不完整，请补充后再继续。",
    "context_stale": "当前业务信息暂时无法核实，请稍后重试。",
    "context_conflict": "当前可用信息存在冲突，需要核实后再继续。",
    "user_input_required": "还需要你补充一项信息才能继续处理。",
    "model_unavailable": "模型服务暂时不可用，请稍后重试。",
    "model_output_invalid": "本次回复无法可靠解析，请重新尝试。",
    "tool_rejected": "该操作不在当前客服可用范围内。",
    "tool_failed": "查询暂时失败，请稍后重试。",
    "policy_blocked": "当前请求不满足确定性业务规则，无法继续操作。",
    "budget_exhausted": "本次处理已达到步骤或资源上限。",
    "verification_failed": "当前结果无法获得足够证据验证，已停止处理。",
}


def attribute_l2_failure(
    *,
    context_missing: bool = False,
    context_stale: bool = False,
    context_conflict: bool = False,
    user_input_required: bool = False,
    model_unavailable: bool = False,
    model_output_invalid: bool = False,
    tool_rejected: bool = False,
    tool_failed: bool = False,
    policy_blocked: bool = False,
    budget_exhausted: bool = False,
    verification_failed: bool = False,
) -> L2FailureAttribution | None:
    """按固定事实优先级返回当前步骤的唯一主失败归因。"""

    ordered = (
        (context_missing, "context_missing"),
        (context_stale, "context_stale"),
        (context_conflict, "context_conflict"),
        (user_input_required, "user_input_required"),
        (model_unavailable, "model_unavailable"),
        (model_output_invalid, "model_output_invalid"),
        (tool_rejected, "tool_rejected"),
        (tool_failed, "tool_failed"),
        (policy_blocked, "policy_blocked"),
        (budget_exhausted, "budget_exhausted"),
        (verification_failed, "verification_failed"),
    )
    for matched, attribution in ordered:
        if matched:
            return attribution
    return None


def public_failure_message(attribution: L2FailureAttribution) -> str:
    """把内部稳定归因映射为不泄露诊断细节的普通对话文案。"""

    return _PUBLIC_FAILURE_MESSAGES[attribution]
