"""验证订单查询 LangGraph 工作流的业务结果和调用轨迹。"""

from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.gateways import Dependencies
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow


def test_valid_order_inquiry_returns_verified_order_and_shipment() -> None:
    """验证有效查询只返回通过身份校验的订单及其物流信息。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="包裹已离开上海转运中心",
        estimated_delivery_at=date(2026, 7, 18),
    )
    interpreter = FakeQueryInterpreter()
    order_gateway = FakeOrderGateway({("user-001", "ORD-001"): order})
    logistics_gateway = FakeLogisticsGateway({"ORD-001": shipment})
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        )
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "查询订单 ORD-001 的物流"}]},
        context=RunContext(user_id="user-001"),
    )

    assert result["status"] == "completed"
    assert result["order"] == order
    assert result["shipment"] == shipment
    assert result["audit"] == [
        "interpreted:order_inquiry",
        "order_queried",
        "shipment_queried",
        "completed",
    ]
    assert interpreter.calls == ["查询订单 ORD-001 的物流"]
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == ["ORD-001"]
    assert "订单 ORD-001 当前状态：已发货" in result["messages"][-1].content
    assert "物流状态：运输中" in result["messages"][-1].content


@pytest.mark.parametrize(
    ("user_id", "order_id"),
    [
        ("user-001", "ORD-999"),
        ("user-002", "ORD-001"),
    ],
)
def test_unavailable_and_unauthorized_orders_share_public_semantics(
    user_id: str,
    order_id: str,
) -> None:
    """验证不存在与越权订单使用相同回复且不查询物流。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    interpreter = FakeQueryInterpreter()
    order_gateway = FakeOrderGateway({("user-001", "ORD-001"): order})
    logistics_gateway = FakeLogisticsGateway({})
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        )
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": f"查询订单 {order_id}"}]},
        context=RunContext(user_id=user_id),
    )

    assert result["status"] == "order_unavailable"
    assert result["error_code"] == "order_unavailable"
    assert result.get("order") is None
    assert result.get("shipment") is None
    assert result["messages"][-1].content == (
        "无法查询该订单，请检查订单号或当前账号。"
    )
    assert order_gateway.calls == [(user_id, order_id)]
    assert logistics_gateway.calls == []


def test_temporary_order_failure_does_not_query_logistics() -> None:
    """验证订单服务暂时失败时使用脱敏回复并停止后续查询。"""

    order_gateway = FakeOrderGateway({}, temporarily_failed=True)
    logistics_gateway = FakeLogisticsGateway({})
    graph = build_workflow(
        Dependencies(
            interpreter=FakeQueryInterpreter(),
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        )
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "查询订单 ORD-001"}]},
        context=RunContext(user_id="user-001"),
    )

    assert result["status"] == "temporarily_failed"
    assert result["error_code"] == "order_temporarily_failed"
    assert result.get("order") is None
    assert result.get("shipment") is None
    assert result["messages"][-1].content == ("订单或物流服务暂时不可用，请稍后重试。")
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == []


@pytest.mark.parametrize(
    ("shipments", "temporarily_failed", "expected_error_code"),
    [
        ({}, False, "shipment_unavailable"),
        ({}, True, "shipment_temporarily_failed"),
    ],
)
def test_logistics_failure_uses_a_safe_public_response(
    shipments: dict[str, ShipmentView],
    temporarily_failed: bool,
    expected_error_code: str,
) -> None:
    """验证物流无结果或暂时失败时不暴露内部错误。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    order_gateway = FakeOrderGateway({("user-001", "ORD-001"): order})
    logistics_gateway = FakeLogisticsGateway(
        shipments,
        temporarily_failed=temporarily_failed,
    )
    graph = build_workflow(
        Dependencies(
            interpreter=FakeQueryInterpreter(),
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        )
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "查询订单 ORD-001"}]},
        context=RunContext(user_id="user-001"),
    )

    assert result["status"] == "temporarily_failed"
    assert result["error_code"] == expected_error_code
    assert result["order"] == order
    assert result.get("shipment") is None
    assert result["messages"][-1].content == ("订单或物流服务暂时不可用，请稍后重试。")
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == ["ORD-001"]


@pytest.mark.parametrize(
    "message",
    [
        "请退款 ORD-001",
        "取消订单 ORD-001",
        "修改地址 ORD-001",
    ],
)
def test_unsupported_write_requests_do_not_call_business_tools(
    message: str,
) -> None:
    """验证未装配退款能力及其他写请求不会触发只读业务工具。"""

    interpreter = FakeQueryInterpreter()
    order_gateway = FakeOrderGateway({})
    logistics_gateway = FakeLogisticsGateway({})
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        )
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": message}]},
        context=RunContext(user_id="user-001"),
    )

    assert result["intent"] == "unsupported_write"
    assert result["status"] == "unsupported"
    assert result["error_code"] == "unsupported_write"
    assert result.get("order") is None
    assert result.get("shipment") is None
    assert result["messages"][-1].content == (
        "当前演示支持经确认的 Mock 退款，暂不支持退货寄回、换货、"
        "取消订单或修改收货信息。"
        "如需申请退款，请明确说明退款原因。"
    )
    assert order_gateway.calls == []
    assert logistics_gateway.calls == []


def test_unknown_intent_is_clarified_then_recovers_to_a_supported_path() -> None:
    """验证模糊售后表达先澄清，补充明确意图后恢复既有查询路径。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="包裹已离开上海转运中心",
        estimated_delivery_at=date(2026, 7, 18),
    )
    interpreter = FakeQueryInterpreter()
    order_gateway = FakeOrderGateway({("user-001", "ORD-001"): order})
    logistics_gateway = FakeLogisticsGateway({"ORD-001": shipment})
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        ),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "clarify-recover-001"}}
    context = RunContext(user_id="user-001")

    awaiting = graph.invoke(
        {"messages": [{"role": "user", "content": "商品质量不行，我要退库呐"}]},
        config=config,
        context=context,
    )
    completed = graph.invoke(
        {"messages": [{"role": "user", "content": "查询订单 ORD-001 的物流"}]},
        config=config,
        context=context,
    )

    assert awaiting["status"] == "awaiting_intent_clarification"
    assert awaiting["intent_clarification_attempts"] == 1
    assert "查询订单或物流" in awaiting["messages"][-1].content
    assert completed["status"] == "completed"
    assert completed["intent_clarification_attempts"] == 0
    assert interpreter.contexts[0] is not None
    assert interpreter.contexts[0].pending_intent_clarification is False
    assert interpreter.contexts[1] is not None
    assert interpreter.contexts[1].pending_intent_clarification is True
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == ["ORD-001"]


def test_unknown_intent_stops_after_two_clarification_questions() -> None:
    """验证连续未知意图达到两轮澄清上限后兜底，且不调用业务工具。"""

    interpreter = FakeQueryInterpreter()
    order_gateway = FakeOrderGateway({})
    logistics_gateway = FakeLogisticsGateway({})
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        ),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "clarify-fallback-001"}}
    context = RunContext(user_id="user-001")

    first = graph.invoke(
        {"messages": [{"role": "user", "content": "你好"}]},
        config=config,
        context=context,
    )
    second = graph.invoke(
        {"messages": [{"role": "user", "content": "还是没说清楚"}]},
        config=config,
        context=context,
    )
    fallback = graph.invoke(
        {"messages": [{"role": "user", "content": "随便吧"}]},
        config=config,
        context=context,
    )

    assert first["status"] == second["status"] == "awaiting_intent_clarification"
    assert first["intent_clarification_attempts"] == 1
    assert second["intent_clarification_attempts"] == 2
    assert fallback["status"] == "intent_unresolved"
    assert fallback["intent_clarification_attempts"] == 0
    assert fallback["error_code"] == "intent_unresolved"
    assert "本次澄清已结束" in fallback["messages"][-1].content
    assert order_gateway.calls == []
    assert logistics_gateway.calls == []


def test_failed_query_clears_business_facts_from_a_previous_turn() -> None:
    """验证同一 thread 的失败查询不会暴露上一轮订单与物流。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="包裹已离开上海转运中心",
        estimated_delivery_at=date(2026, 7, 18),
    )
    order_gateway = FakeOrderGateway({("user-001", "ORD-001"): order})
    logistics_gateway = FakeLogisticsGateway({"ORD-001": shipment})
    graph = build_workflow(
        Dependencies(
            interpreter=FakeQueryInterpreter(),
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        ),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "clear-stale-order-001"}}
    context = RunContext(user_id="user-001")

    completed = graph.invoke(
        {"messages": [{"role": "user", "content": "查询订单 ORD-001"}]},
        config=config,
        context=context,
    )
    unavailable = graph.invoke(
        {"messages": [{"role": "user", "content": "查询订单 ORD-999"}]},
        config=config,
        context=context,
    )

    assert completed["order"] == order
    assert completed["shipment"] == shipment
    assert unavailable["status"] == "order_unavailable"
    assert unavailable.get("order") is None
    assert unavailable.get("shipment") is None
    assert logistics_gateway.calls == ["ORD-001"]


def test_missing_order_id_can_be_supplied_in_the_same_thread() -> None:
    """验证等待订单号时不调用工具，补充后继续完成原查询。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="包裹已离开上海转运中心",
        estimated_delivery_at=date(2026, 7, 18),
    )
    interpreter = FakeQueryInterpreter()
    order_gateway = FakeOrderGateway({("user-001", "ORD-001"): order})
    logistics_gateway = FakeLogisticsGateway({"ORD-001": shipment})
    graph = build_workflow(
        Dependencies(
            interpreter=interpreter,
            order_gateway=order_gateway,
            logistics_gateway=logistics_gateway,
        ),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "missing-order-001"}}
    context = RunContext(user_id="user-001")

    awaiting = graph.invoke(
        {"messages": [{"role": "user", "content": "帮我查一下物流"}]},
        config=config,
        context=context,
    )

    assert awaiting["status"] == "awaiting_order_id"
    assert awaiting["intent"] == "order_inquiry"
    assert awaiting["order_id"] is None
    assert awaiting["audit"] == [
        "interpreted:order_inquiry",
        "awaiting_order_id",
    ]
    assert awaiting["messages"][-1].content == "请提供需要查询的订单号。"
    assert order_gateway.calls == []
    assert logistics_gateway.calls == []

    completed = graph.invoke(
        {"messages": [{"role": "user", "content": "ORD-001"}]},
        config=config,
        context=context,
    )

    assert completed["status"] == "completed"
    assert completed["order"] == order
    assert completed["shipment"] == shipment
    assert completed["audit"] == [
        "interpreted:order_inquiry",
        "awaiting_order_id",
        "interpreted:order_inquiry",
        "order_queried",
        "shipment_queried",
        "completed",
    ]
    assert [message.content for message in completed["messages"]] == [
        "帮我查一下物流",
        "请提供需要查询的订单号。",
        "ORD-001",
        "订单 ORD-001 当前状态：已发货。物流状态：运输中。"
        "最近事件：包裹已离开上海转运中心。预计送达：2026-07-18。",
    ]
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == ["ORD-001"]
