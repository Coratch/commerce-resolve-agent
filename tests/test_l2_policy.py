"""验证 L2 决策 Schema、升级权限和 Harness 预算规则。"""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from commerce_resolve.l2_models import (
    GetOrderCall,
    L2Decision,
    L2RuntimeState,
    ToolCallDecision,
)
from commerce_resolve.l2_policy import (
    budget_after_model_call,
    budget_after_tool_call,
    check_model_budget,
    decide_l2_upgrade,
    tool_action_signature,
    validate_tool_call,
)

DECISION_ADAPTER = TypeAdapter(L2Decision)
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _runtime() -> L2RuntimeState:
    """构造允许订单查询且预算尚未消耗的活动 L2 State。"""

    return L2RuntimeState(
        case_id="case-001",
        phase="active",
        issue_summary="订单物流信息互相矛盾",
        allowed_tools=("get_order",),
    )


def test_decision_schema_rejects_unknown_kind_tool_and_extra_fields() -> None:
    """验证模型不能发明决策、工具或额外授权字段。"""

    with pytest.raises(ValidationError):
        DECISION_ADAPTER.validate_python({"kind": "delegate_to_human"})
    with pytest.raises(ValidationError):
        DECISION_ADAPTER.validate_python(
            {"kind": "tool_call", "call": {"tool": "run_sql"}}
        )
    with pytest.raises(ValidationError):
        DECISION_ADAPTER.validate_python(
            {
                "kind": "tool_call",
                "call": {"tool": "get_order", "order_id": "ORD-001"},
                "approved": True,
            }
        )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"registered": False}, "l2_registered_required"),
        ({"llm_allowed": False}, "l2_model_not_authorized"),
        ({"quota_remaining": 0}, "l2_quota_exceeded"),
        ({"sale_support_candidate": False}, "l2_out_of_scope"),
        ({"has_conflicting_interrupt": True}, "l2_pending_action_conflict"),
        ({"has_active_case": True}, "l2_case_already_active"),
    ],
)
def test_upgrade_policy_uses_only_trusted_capabilities(
    overrides: dict[str, object],
    expected: str,
) -> None:
    """验证每个可信服务端边界都能在展示升级前拒绝请求。"""

    inputs: dict[str, object] = {
        "registered": True,
        "llm_allowed": True,
        "quota_remaining": 3,
        "sale_support_candidate": True,
        "has_conflicting_interrupt": False,
        "has_active_case": False,
    }
    inputs.update(overrides)

    decision = decide_l2_upgrade(**inputs)  # type: ignore[arg-type]

    assert decision.allowed is False
    assert decision.reason_code == expected


def test_tool_policy_stops_repeated_action_and_budget_overrun_before_execution() -> (
    None
):
    """验证重复无进展和预算耗尽都在工具产生副作用前被拒绝。"""

    call = GetOrderCall(tool="get_order", order_id="ORD-001")
    runtime = _runtime()
    signature = tool_action_signature(call)
    first_budget = budget_after_tool_call(
        runtime.budget,
        signature=signature,
        succeeded=True,
        duration_ms=5,
    )
    repeated_budget = budget_after_tool_call(
        first_budget,
        signature=signature,
        succeeded=True,
        duration_ms=5,
    )

    assert validate_tool_call(runtime, call) is None
    assert (
        validate_tool_call(runtime.model_copy(update={"budget": repeated_budget}), call)
        == "no_progress"
    )
    exhausted = runtime.model_copy(
        update={
            "budget": runtime.budget.model_copy(
                update={"tool_calls_used": runtime.budget_limits.max_tool_calls}
            )
        }
    )
    assert validate_tool_call(exhausted, call) == "tool_budget_exhausted"


def test_model_budget_counts_calls_tokens_steps_and_time() -> None:
    """验证模型调用后的四类累计量都会参与下一次调用门禁。"""

    runtime = _runtime()
    used = budget_after_model_call(
        runtime.budget,
        charged_tokens=29_500,
        duration_ms=120_000,
    )
    updated = runtime.model_copy(update={"budget": used})

    assert used.steps_used == 1
    assert used.model_calls_used == 1
    assert used.estimated_tokens_used == 29_500
    assert check_model_budget(updated, projected_tokens=600) == "token_budget_exhausted"


def test_scripted_tool_decision_keeps_strict_call_type() -> None:
    """验证判别联合把合法调用解析为确定工具类型。"""

    decision = DECISION_ADAPTER.validate_python(
        {
            "kind": "tool_call",
            "call": {"tool": "get_order", "order_id": "ORD-001"},
        }
    )

    assert isinstance(decision, ToolCallDecision)
    assert decision.call.order_id == "ORD-001"
