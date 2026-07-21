"""验证退款主图的资格、暂停、拒绝、执行、过期和失败路径。"""

from datetime import date
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from commerce_resolve.access import BusinessScope
from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.fake_refunds import FakeRefundGateway
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.gateways import Dependencies
from commerce_resolve.models import RefundContext
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow

USER_ID = "refund-user"
WORKSPACE_ID = "refund-workspace"
THREAD_ID = "refund-thread"


@pytest.fixture
def policy_repository(tmp_path: Path) -> SqlitePolicyRepository:
    """构建包含 v0.4 资格事实的临时只读政策索引。"""

    database = tmp_path / "policy.sqlite"
    build_policy_index(Path("data/policies"), database)
    return SqlitePolicyRepository(database, source_root=Path("data/policies"))


def _context(**changes: object) -> RefundContext:
    """构造默认符合发货前整单退款条件的业务事实。"""

    values: dict[str, object] = {
        "order_id": "ORD-001",
        "order_status": "processing",
        "shipment_status": "preparing",
        "shipment_last_event": "等待揽收",
        "payment_id": "payment-001",
        "paid_amount_minor": 12990,
        "currency": "CNY",
        "channel": "mock_card",
        "payment_status": "settled",
    }
    values.update(changes)
    return RefundContext.model_validate(values)


def _graph(
    policy_repository: SqlitePolicyRepository,
    gateway: FakeRefundGateway,
):
    """使用 Fake Interpreter、业务工具和内存 Checkpointer 构建唯一主图。"""

    return build_workflow(
        Dependencies(
            interpreter=FakeQueryInterpreter(),
            order_gateway=FakeOrderGateway({}),
            logistics_gateway=FakeLogisticsGateway({}),
            policy_repository=policy_repository,
            refund_gateway=gateway,
        ),
        InMemorySaver(),
    )


def _run_context() -> RunContext:
    """返回注册用户退款 Graph 所需的可信运行上下文。"""

    return RunContext(
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        access_mode="registered",
        as_of=date(2026, 7, 17),
        task_id=THREAD_ID,
    )


def _start(graph, message: str = "请退款 ORD-001，商品有质量问题"):
    """用固定 thread 启动一轮退款申请。"""

    return graph.invoke(
        {"messages": [{"role": "user", "content": message}]},
        config={"configurable": {"thread_id": THREAD_ID}},
        context=_run_context(),
    )


def _resume(graph, action_id: str, decision: str):
    """使用同一 thread 和服务端 action 恢复审批。"""

    return graph.invoke(
        Command(resume={"action_id": action_id, "decision": decision}),
        config={"configurable": {"thread_id": THREAD_ID}},
        context=_run_context(),
    )


def test_refund_request_collects_missing_reason(
    policy_repository: SqlitePolicyRepository,
) -> None:
    """验证缺少原因时只请求补充，不读取或写入退款业务。"""

    gateway = FakeRefundGateway({(USER_ID, WORKSPACE_ID, "ORD-001"): _context()})
    graph = _graph(policy_repository, gateway)

    result = _start(graph, "请退款 ORD-001")

    assert result["status"] == "awaiting_refund_context"
    assert gateway.reserve_calls == 0
    assert gateway.execute_calls == 0


def test_eligible_refund_pauses_with_preview_and_no_refund_write(
    policy_repository: SqlitePolicyRepository,
) -> None:
    """验证完整请求生成带三条政策引用的 R2 预览并真实中断。"""

    gateway = FakeRefundGateway({(USER_ID, WORKSPACE_ID, "ORD-001"): _context()})
    graph = _graph(policy_repository, gateway)

    result = _start(graph)
    snapshot = graph.get_state({"configurable": {"thread_id": THREAD_ID}})

    assert result["status"] == "refund_awaiting_approval"
    assert result["refund_preview"].amount_minor == 12990
    assert len(result["refund_preview"].citations) == 3
    assert snapshot.next == ("await_refund_approval",)
    assert len(snapshot.interrupts) == 1
    assert gateway.reserve_calls == 1
    assert gateway.execute_calls == 0
    assert (
        gateway.get_refund_by_action(
            BusinessScope(
                user_id=USER_ID,
                workspace_id=WORKSPACE_ID,
                access_mode="registered",
            ),
            result["refund_preview"].action_id,
        )
        is None
    )


def test_refund_request_combines_order_and_reason_across_turns(
    policy_repository: SqlitePolicyRepository,
) -> None:
    """验证 Checkpoint 只保留最小待补信息，并在下一轮形成同一退款预览。"""

    gateway = FakeRefundGateway({(USER_ID, WORKSPACE_ID, "ORD-001"): _context()})
    graph = _graph(policy_repository, gateway)
    config = {"configurable": {"thread_id": THREAD_ID}}
    first = graph.invoke(
        {"messages": [{"role": "user", "content": "我要退款，商品质量有问题"}]},
        config=config,
        context=_run_context(),
    )

    second = graph.invoke(
        {"messages": [{"role": "user", "content": "ORD-001"}]},
        config=config,
        context=_run_context(),
    )

    assert first["status"] == "awaiting_refund_context"
    assert second["status"] == "refund_awaiting_approval"
    assert second["refund_reason"].code == "quality_issue"


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"payment_id": None, "payment_status": None}, "refund_payment_missing"),
        ({"payment_status": "pending"}, "refund_payment_not_settled"),
        ({"order_status": "shipped"}, "refund_requires_return_flow"),
        ({"shipment_status": "in_transit"}, "refund_business_facts_conflict"),
        ({"has_conflicting_refund": True}, "refund_conflict"),
    ],
)
def test_ineligible_refund_never_reserves_or_executes(
    policy_repository: SqlitePolicyRepository,
    changes: dict[str, object],
    reason_code: str,
) -> None:
    """验证状态矩阵的不符合资格分支保持零退款副作用。"""

    gateway = FakeRefundGateway(
        {(USER_ID, WORKSPACE_ID, "ORD-001"): _context(**changes)}
    )
    graph = _graph(policy_repository, gateway)

    result = _start(graph)

    assert result["status"] == "refund_ineligible"
    assert result["error_code"] == reason_code
    assert gateway.reserve_calls == 0
    assert gateway.execute_calls == 0


def test_rejecting_preview_has_zero_refund_side_effect(
    policy_repository: SqlitePolicyRepository,
) -> None:
    """验证拒绝路径结束任务且不调用执行 Gateway。"""

    gateway = FakeRefundGateway({(USER_ID, WORKSPACE_ID, "ORD-001"): _context()})
    graph = _graph(policy_repository, gateway)
    paused = _start(graph)

    result = _resume(graph, paused["refund_preview"].action_id, "reject")

    assert result["status"] == "refund_rejected"
    assert gateway.execute_calls == 0


def test_approved_refund_is_executed_once_and_verified(
    policy_repository: SqlitePolicyRepository,
) -> None:
    """验证批准后执行并通过独立回读，最终状态才是 completed。"""

    gateway = FakeRefundGateway({(USER_ID, WORKSPACE_ID, "ORD-001"): _context()})
    graph = _graph(policy_repository, gateway)
    paused = _start(graph)

    result = _resume(graph, paused["refund_preview"].action_id, "approve")

    assert result["status"] == "refund_completed"
    assert result["refund_verification"].verified is True
    assert gateway.execute_calls == 1
    assert gateway.verify_calls == 1


def test_changed_business_facts_make_preview_stale(
    policy_repository: SqlitePolicyRepository,
) -> None:
    """验证批准前支付或订单事实变化会关闭旧预览而不执行。"""

    gateway = FakeRefundGateway({(USER_ID, WORKSPACE_ID, "ORD-001"): _context()})
    graph = _graph(policy_repository, gateway)
    paused = _start(graph)
    gateway.replace_context(
        BusinessScope(
            user_id=USER_ID,
            workspace_id=WORKSPACE_ID,
            access_mode="registered",
        ),
        _context(payment_status="failed"),
    )

    result = _resume(graph, paused["refund_preview"].action_id, "approve")

    assert result["status"] == "refund_preview_stale"
    assert gateway.execute_calls == 0


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("business_rejected", "refund_failed"),
        ("fail_before_write", "refund_failed"),
        ("unknown_after_write", "refund_result_unknown"),
        ("verification_mismatch", "refund_failed"),
    ],
)
def test_gateway_failures_never_claim_success(
    policy_repository: SqlitePolicyRepository,
    mode: str,
    expected_status: str,
) -> None:
    """验证有限失败、未知和不一致结果都不会伪装为退款完成。"""

    gateway = FakeRefundGateway(
        {(USER_ID, WORKSPACE_ID, "ORD-001"): _context()},
        execution_mode=mode,
    )
    graph = _graph(policy_repository, gateway)
    paused = _start(graph)

    result = _resume(graph, paused["refund_preview"].action_id, "approve")

    assert result["status"] == expected_status
    assert result["status"] != "refund_completed"
