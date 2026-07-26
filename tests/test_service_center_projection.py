"""验证统一服务状态映射覆盖退款和 L2 全部领域状态。"""

import pytest

from commerce_resolve.service_center import map_l2_status, map_refund_status


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("awaiting_approval", "waiting_user"),
        ("executing", "in_progress"),
        ("completed", "completed"),
        ("rejected", "cancelled"),
        ("stale", "needs_attention"),
        ("failed", "needs_attention"),
        ("unknown", "needs_attention"),
        ("verification_failed", "needs_attention"),
    ],
)
def test_refund_service_status_mapping(source: str, expected: str) -> None:
    """验证每个退款动作状态都有确定客户映射。"""

    assert map_refund_status(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("l2_active", "in_progress"),
        ("l2_waiting_user", "waiting_user"),
        ("l2_waiting_approval", "waiting_user"),
        ("l2_resolved", "completed"),
        ("l2_cancelled", "cancelled"),
        ("l2_unresolved", "needs_attention"),
        ("l2_budget_exhausted", "needs_attention"),
        ("l2_stopped", "needs_attention"),
    ],
)
def test_l2_service_status_mapping(source: str, expected: str) -> None:
    """验证每个 L2 Case 状态都有确定客户映射。"""

    assert map_l2_status(source) == expected
