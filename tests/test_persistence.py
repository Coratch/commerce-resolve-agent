"""验证 SQLite Checkpointer 的跨实例恢复与会话隔离。"""

from datetime import date
from pathlib import Path

import pytest

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.gateways import Dependencies
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow

SOURCE_POLICIES = Path(__file__).parent.parent / "data" / "policies"
AS_OF = date(2026, 7, 17)


def _build_dependencies() -> tuple[
    Dependencies,
    FakeQueryInterpreter,
    FakeOrderGateway,
    FakeLogisticsGateway,
]:
    """构造可检查调用轨迹的确定性测试依赖。"""

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
    dependencies = Dependencies(
        interpreter=interpreter,
        order_gateway=order_gateway,
        logistics_gateway=logistics_gateway,
    )
    return dependencies, interpreter, order_gateway, logistics_gateway


def test_sqlite_restores_waiting_state_in_a_new_graph(tmp_path) -> None:
    """验证关闭连接并新建 Graph 后仍能继续等待中的查询。"""

    database = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "restore-order-001"}}
    context = RunContext(user_id="user-001")
    first_dependencies, _, _, _ = _build_dependencies()

    with open_sqlite_checkpointer(database) as checkpointer:
        first_graph = build_workflow(first_dependencies, checkpointer)
        awaiting = first_graph.invoke(
            {"messages": [{"role": "user", "content": "帮我查一下物流"}]},
            config=config,
            context=context,
        )

    assert awaiting["status"] == "awaiting_order_id"

    second_dependencies, _, order_gateway, logistics_gateway = _build_dependencies()
    with open_sqlite_checkpointer(database) as checkpointer:
        second_graph = build_workflow(second_dependencies, checkpointer)
        restored = second_graph.get_state(config)
        completed = second_graph.invoke(
            {"messages": [{"role": "user", "content": "ORD-001"}]},
            config=config,
            context=context,
        )

    assert restored.values["status"] == "awaiting_order_id"
    assert restored.values["owner_user_id"] == "user-001"
    assert completed["status"] == "completed"
    assert [message.content for message in completed["messages"]] == [
        "帮我查一下物流",
        "请提供需要查询的订单号。",
        "ORD-001",
        "订单 ORD-001 当前状态：已发货。物流状态：运输中。"
        "最近事件：包裹已离开上海转运中心。预计送达：2026-07-18。",
    ]
    assert order_gateway.calls == [("user-001", "ORD-001")]
    assert logistics_gateway.calls == ["ORD-001"]


def test_sqlite_keeps_threads_isolated(tmp_path) -> None:
    """验证一个 thread 的状态不会出现在另一个 thread 中。"""

    database = tmp_path / "checkpoints.sqlite"
    first_config = {"configurable": {"thread_id": "thread-a"}}
    second_config = {"configurable": {"thread_id": "thread-b"}}
    dependencies, _, _, _ = _build_dependencies()

    with open_sqlite_checkpointer(database) as checkpointer:
        graph = build_workflow(dependencies, checkpointer)
        graph.invoke(
            {"messages": [{"role": "user", "content": "帮我查一下物流"}]},
            config=first_config,
            context=RunContext(user_id="user-001"),
        )

        assert graph.get_state(second_config).values == {}


def test_sqlite_rejects_a_different_user_on_the_same_thread(tmp_path) -> None:
    """验证不同用户不能继续已经绑定身份的 thread。"""

    database = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "bound-user-001"}}
    first_dependencies, _, _, _ = _build_dependencies()

    with open_sqlite_checkpointer(database) as checkpointer:
        first_graph = build_workflow(first_dependencies, checkpointer)
        first_graph.invoke(
            {"messages": [{"role": "user", "content": "帮我查一下物流"}]},
            config=config,
            context=RunContext(user_id="user-001"),
        )

    second_dependencies, interpreter, order_gateway, logistics_gateway = (
        _build_dependencies()
    )
    with open_sqlite_checkpointer(database) as checkpointer:
        second_graph = build_workflow(second_dependencies, checkpointer)
        with pytest.raises(ValueError, match="无法继续该会话"):
            second_graph.invoke(
                {"messages": [{"role": "user", "content": "ORD-001"}]},
                config=config,
                context=RunContext(user_id="user-002"),
            )
        current_state = second_graph.get_state(config).values

    assert current_state["owner_user_id"] == "user-001"
    assert current_state.get("order") is None
    assert current_state.get("shipment") is None
    assert interpreter.calls == []
    assert order_gateway.calls == []
    assert logistics_gateway.calls == []


def test_sqlite_restores_pending_policy_context_in_a_new_graph(tmp_path) -> None:
    """验证关闭连接并重建仓库和 Graph 后可继续待补政策问题。"""

    checkpoint_database = tmp_path / "checkpoints.sqlite"
    policy_database = tmp_path / "policy-index.sqlite"
    build_policy_index(SOURCE_POLICIES, policy_database)
    config = {"configurable": {"thread_id": "restore-policy-001"}}
    context = RunContext(user_id="user-001", as_of=AS_OF)

    first_interpreter = FakeQueryInterpreter()
    with open_sqlite_checkpointer(checkpoint_database) as checkpointer:
        first_graph = build_workflow(
            Dependencies(
                interpreter=first_interpreter,
                order_gateway=FakeOrderGateway({}),
                logistics_gateway=FakeLogisticsGateway({}),
                policy_repository=SqlitePolicyRepository(
                    policy_database,
                    source_root=SOURCE_POLICIES,
                ),
            ),
            checkpointer,
        )
        awaiting = first_graph.invoke(
            {"messages": [{"role": "user", "content": "已拆封的商品还能退吗？"}]},
            config=config,
            context=context,
        )

    assert awaiting["status"] == "awaiting_policy_context"
    assert awaiting["pending_policy_query"].opened is True

    second_interpreter = FakeQueryInterpreter()
    with open_sqlite_checkpointer(checkpoint_database) as checkpointer:
        second_graph = build_workflow(
            Dependencies(
                interpreter=second_interpreter,
                order_gateway=FakeOrderGateway({}),
                logistics_gateway=FakeLogisticsGateway({}),
                policy_repository=SqlitePolicyRepository(
                    policy_database,
                    source_root=SOURCE_POLICIES,
                ),
            ),
            checkpointer,
        )
        restored = second_graph.get_state(config)
        answered = second_graph.invoke(
            {"messages": [{"role": "user", "content": "普通服饰"}]},
            config=config,
            context=context,
        )

    assert restored.values["status"] == "awaiting_policy_context"
    assert tuple(restored.values["missing_policy_dimensions"]) == ("product_category",)
    assert answered["status"] == "policy_answered"
    assert answered["selected_policy_fact_ids"] == ("return.conditions.opened-general",)
    assert second_interpreter.contexts[0] is not None
    assert second_interpreter.contexts[0].previous_policy_query.opened is True
