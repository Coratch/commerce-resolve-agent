"""验证 v1.3 单 Agent 组合咨询、事实预算与安全停止语义。"""

from datetime import date
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.gateways import Dependencies
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow

AS_OF = date(2026, 7, 23)


def _guidance_graph(
    tmp_path: Path,
    *,
    order_failed: bool = False,
    shipment_failed: bool = False,
):
    """构建含真实政策索引和可观察 Fake Gateway 的组合咨询图。"""

    policy_database = tmp_path / "policy.sqlite"
    build_policy_index(Path("data/policies"), policy_database)
    interpreter = FakeQueryInterpreter()
    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="delivered",
    )
    shipment = ShipmentView(
        order_id="ORD-001",
        status="delivered",
        last_event="包裹已由本人签收",
        estimated_delivery_at=AS_OF,
    )
    order_gateway = FakeOrderGateway(
        {("user-001", "ORD-001"): order},
        temporarily_failed=order_failed,
    )
    logistics_gateway = FakeLogisticsGateway(
        {"ORD-001": shipment},
        temporarily_failed=shipment_failed,
    )
    repository = SqlitePolicyRepository(
        policy_database,
        source_root=Path("data/policies"),
    )
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
            policy_repository=repository,
        ),
        checkpointer=InMemorySaver(),
    )
    return graph, interpreter, order_gateway, logistics_gateway, repository


def _invoke(graph, message: str, thread_id: str = "guidance-001"):
    """以固定可信身份运行一次组合咨询。"""

    return graph.invoke(
        {"messages": [{"role": "user", "content": message}]},
        config={"configurable": {"thread_id": thread_id}},
        context=RunContext(user_id="user-001", as_of=AS_OF),
    )


def test_combined_shipping_and_refund_question_uses_each_source_once(
    tmp_path: Path,
) -> None:
    """验证组合问题只解释一次，并各查询一次订单、物流和政策。"""

    graph, interpreter, order_gateway, logistics_gateway, repository = _guidance_graph(
        tmp_path
    )

    result = _invoke(graph, "ORD-001 的物流到哪了，并且能不能退款？")

    resolution = result["service_resolution"]
    assert result["intent"] == "service_guidance"
    assert result["status"] == "service_guidance_completed"
    assert set(result["service_concerns"]) == {
        "shipment_status",
        "refund_eligibility",
    }
    assert resolution.stop_reason == "completed"
    assert "request_refund" in resolution.allowed_actions
    assert {item.category for item in resolution.verified_facts} == {
        "order",
        "shipment",
        "policy",
    }
    assert len(interpreter.calls) == 1
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == ["ORD-001"]
    assert len(repository.calls) == 1
    assert "refund_preview" not in result or result["refund_preview"] is None


def test_combined_consultation_never_executes_refund(
    tmp_path: Path,
) -> None:
    """验证资格咨询只返回候选动作，不创建或执行资金动作。"""

    graph, _interpreter, _orders, _shipments, _repository = _guidance_graph(tmp_path)

    result = _invoke(graph, "ORD-001 已经签收，物流状态和退款资格是什么？")

    assert result["service_resolution"].stop_reason == "completed"
    assert result.get("refund_action_id") is None
    assert result.get("refund_result") is None
    assert result.get("refund_verification") is None


def test_missing_order_returns_partial_resolution_without_tool_guess(
    tmp_path: Path,
) -> None:
    """验证缺少订单号时不调用业务工具，并明确要求补充信息。"""

    graph, interpreter, order_gateway, logistics_gateway, repository = _guidance_graph(
        tmp_path
    )

    result = _invoke(graph, "物流到哪了，并且能不能退款？")

    resolution = result["service_resolution"]
    assert resolution.stop_reason == "needs_user_input"
    assert "可访问的订单号或订单事实" in resolution.missing_information
    assert "provide_information" in resolution.allowed_actions
    assert len(interpreter.calls) == 1
    assert order_gateway.calls == []
    assert logistics_gateway.calls == []
    assert len(repository.calls) == 1


def test_gateway_failure_has_finite_stop_and_l2_option(tmp_path: Path) -> None:
    """验证只读工具失败时有限停止，不继续伪造成功事实。"""

    graph, _interpreter, order_gateway, logistics_gateway, repository = _guidance_graph(
        tmp_path, order_failed=True
    )

    result = _invoke(graph, "ORD-001 的物流到哪了，并且能不能退款？")

    resolution = result["service_resolution"]
    assert resolution.stop_reason == "tool_failed"
    assert "upgrade_l2" in resolution.allowed_actions
    assert not any(item.category == "order" for item in resolution.verified_facts)
    assert len(order_gateway.calls) == 1
    assert logistics_gateway.calls == []
    assert len(repository.calls) == 1
