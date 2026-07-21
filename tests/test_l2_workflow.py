"""验证 AI 二线升级、受控 Agent Loop、中断恢复和公开轨迹。"""

from datetime import UTC, date, datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.fake_l2_agent import ScriptedL2Agent
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository
from commerce_resolve.gateways import Dependencies
from commerce_resolve.l2_gateways import (
    L2AgentModel,
    L2Dependencies,
    L2ModelOutputInvalidError,
)
from commerce_resolve.l2_models import (
    AnswerDecision,
    AskUserDecision,
    GetOrderCall,
    GetShipmentCall,
    L2ModelRequest,
    L2ModelResult,
    L2Observation,
    L2ObservationRefreshResult,
    ToolCallDecision,
)
from commerce_resolve.l2_tools import L2ToolRegistry
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _build_graph(
    tmp_path: Path,
    agent: L2AgentModel,
    *,
    freshness_reader: object | None = None,
):
    """创建迁移数据库、Fake 业务工具和启用 L2 的唯一主图。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine, now_provider=lambda: NOW)
    invite = business.create_invitation()
    registration = business.register(
        username="l2.workflow",
        password="correct horse battery",
        invitation_code=invite.code,
    )
    conversation = business.create_conversation(
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    user_id = registration.user.id
    workspace_id = registration.workspace.id
    thread_id = conversation.thread_id
    repository = SqliteL2CaseRepository(engine, now_provider=lambda: NOW)
    order = OrderView(order_id="ORD-001", user_id=user_id, status="shipped")
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="物流状态与页面展示不一致",
        estimated_delivery_at=date(2026, 7, 22),
    )
    dependencies = Dependencies(
        interpreter=FakeQueryInterpreter(),
        order_gateway=FakeOrderGateway({(user_id, "ORD-001"): order}),
        logistics_gateway=FakeLogisticsGateway({"ORD-001": shipment}),
        l2=L2Dependencies(
            agent_model=agent,
            case_repository=repository,
            tool_registry=L2ToolRegistry(),
            freshness_reader=freshness_reader,  # type: ignore[arg-type]
            daily_call_limit=20,
            clock=lambda: NOW,
        ),
    )
    graph = build_workflow(
        dependencies,
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    return graph, repository, engine, user_id, workspace_id, thread_id


class _InvalidOutputAgent:
    """模拟 Provider 已返回但结构化输出无效的模型适配器。"""

    def __init__(self) -> None:
        """初始化请求记录。"""

        self.requests: list[L2ModelRequest] = []

    @property
    def model_name(self) -> str:
        """返回测试使用的稳定模型名。"""

        return "invalid-output-l2"

    def decide(self, request: L2ModelRequest) -> L2ModelResult:
        """记录请求后模拟 JSON 或 Schema 校验失败。"""

        self.requests.append(request)
        raise L2ModelOutputInvalidError("invalid structured output")


class _AlwaysStaleReader:
    """把已有业务 Observation 标记为无法重新验证。"""

    def refresh(
        self,
        observation: L2Observation,
        **_: object,
    ) -> L2ObservationRefreshResult:
        """返回 stale 结果且不回传旧 Observation。"""

        del observation
        return L2ObservationRefreshResult(
            freshness="stale",
            result_code="source_unavailable",
        )


def _context(user_id: str, workspace_id: str, thread_id: str) -> RunContext:
    """返回允许当前注册用户使用 L2 的可信运行上下文。"""

    return RunContext(
        user_id=user_id,
        workspace_id=workspace_id,
        access_mode="registered",
        as_of=date(2026, 7, 20),
        task_id=thread_id,
        subject_id=user_id,
        l2_allowed=True,
        l2_quota_remaining=20,
    )


def _start(
    graph,
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
):
    """提交明确升级请求并返回首次中断结果。"""

    return graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "请升级二线客服处理 ORD-001 的物流冲突",
                }
            ]
        },
        config={"configurable": {"thread_id": thread_id}},
        context=_context(user_id, workspace_id, thread_id),
    )


def _confirm(
    graph,
    preview_id: str,
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
):
    """使用结构化 preview 标识确认升级并恢复同一任务。"""

    return graph.invoke(
        Command(resume={"preview_id": preview_id, "decision": "confirm"}),
        config={"configurable": {"thread_id": thread_id}},
        context=_context(user_id, workspace_id, thread_id),
    )


def test_upgrade_preview_and_cancel_have_zero_l2_side_effects(tmp_path: Path) -> None:
    """验证确认前及取消后都没有 Case、模型调用或工具写入。"""

    graph, repository, engine, user_id, workspace_id, thread_id = _build_graph(
        tmp_path,
        ScriptedL2Agent([]),
    )

    paused = _start(
        graph,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})

    assert paused["status"] == "l2_awaiting_confirmation"
    assert snapshot.next == ("l2_await_upgrade_confirmation",)
    assert repository.count_cases() == repository.count_model_calls() == 0

    preview = paused["l2_upgrade_preview"]
    cancelled = graph.invoke(
        Command(resume={"preview_id": preview.preview_id, "decision": "cancel"}),
        config={"configurable": {"thread_id": thread_id}},
        context=_context(user_id, workspace_id, thread_id),
    )

    assert cancelled["status"] == "l2_cancelled"
    assert repository.count_cases() == repository.count_model_calls() == 0
    engine.dispose()


def test_confirmed_l2_loop_uses_two_tools_and_resolves_with_evidence(
    tmp_path: Path,
) -> None:
    """验证确认后使用两类可信证据完成，并保存 Case 与公开轨迹。"""

    agent = ScriptedL2Agent(
        [
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id="ORD-001"),
            ),
            ToolCallDecision(
                kind="tool_call",
                call=GetShipmentCall(tool="get_shipment", order_id="ORD-001"),
            ),
            AnswerDecision(
                kind="answer",
                answer="订单已发货，物流当前仍在运输中。",
                evidence_ids=(
                    "order:ORD-001:shipped",
                    "shipment:ORD-001:in_transit",
                ),
            ),
        ]
    )
    graph, repository, engine, user_id, workspace_id, thread_id = _build_graph(
        tmp_path,
        agent,
    )
    preview = _start(
        graph,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )["l2_upgrade_preview"]

    result = _confirm(
        graph,
        preview.preview_id,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )

    assert result["status"] == "l2_resolved"
    assert "物流当前仍在运输中" in result["messages"][-1].content
    runtime = result["l2_runtime"]
    case = repository.get_authorized_case(
        case_id=runtime.case_id,
        subject_id=user_id,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    assert case is not None and case.status == "l2_resolved"
    assert case.usage.model_calls_used == 3
    assert case.usage.tool_calls_used == 2
    assert repository.count_manifests() == repository.count_model_calls() == 3
    assert all(request.context_policy_version == "v0.7.0" for request in agent.requests)
    assert all(request.context.allowed_tools for request in agent.requests)
    events = repository.list_events(
        case_id=runtime.case_id,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    assert {event.tool_category for event in events} >= {
        "get_order",
        "get_shipment",
    }
    assert all("prompt" not in str(event).lower() for event in events)
    engine.dispose()


def test_ask_user_interrupt_resumes_without_reentering_interpreter(
    tmp_path: Path,
) -> None:
    """验证二线追问跨中断恢复后继续 Loop，而不是重新执行一线识别。"""

    agent = ScriptedL2Agent(
        [
            AskUserDecision(
                kind="ask_user",
                question="请确认需要核对的订单号。",
                expected_field="order_id",
            ),
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id="ORD-001"),
            ),
            AnswerDecision(
                kind="answer",
                answer="已确认订单当前为已发货状态。",
                evidence_ids=("order:ORD-001:shipped",),
            ),
        ]
    )
    graph, repository, engine, user_id, workspace_id, thread_id = _build_graph(
        tmp_path,
        agent,
    )
    preview = _start(
        graph,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )["l2_upgrade_preview"]
    waiting = _confirm(
        graph,
        preview.preview_id,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})

    assert waiting["status"] == "l2_waiting_user"
    assert snapshot.next == ("l2_await_user_input",)
    assert len(agent.requests) == 1

    resolved = graph.invoke(
        Command(resume={"message": "订单号是 ORD-001"}),
        config={"configurable": {"thread_id": thread_id}},
        context=_context(user_id, workspace_id, thread_id),
    )

    assert resolved["status"] == "l2_resolved"
    assert len(agent.requests) == 3
    assert repository.count_cases() == 1
    engine.dispose()


def test_manifest_persistence_failure_prevents_model_call(tmp_path: Path) -> None:
    """验证 Manifest 写入失败时安全停止且 Provider 调用数保持为零。"""

    agent = ScriptedL2Agent(
        [
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id="ORD-001"),
            )
        ]
    )
    graph, repository, engine, user_id, workspace_id, thread_id = _build_graph(
        tmp_path,
        agent,
    )

    def reject_manifest(**_: object) -> None:
        """模拟本地 Manifest 持久化失败。"""

        raise RuntimeError("manifest unavailable")

    repository.save_manifest_once = reject_manifest  # type: ignore[method-assign]
    preview = _start(
        graph,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )["l2_upgrade_preview"]

    result = _confirm(
        graph,
        preview.preview_id,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )

    assert result["status"] == "l2_stopped"
    assert result["l2_runtime"].failure_attribution == "context_missing"
    assert agent.requests == []
    assert repository.count_model_calls() == 0
    engine.dispose()


def test_stale_observation_stops_before_next_model_call(tmp_path: Path) -> None:
    """验证旧业务事实无法刷新时不会回退旧值生成第二次模型结论。"""

    agent = ScriptedL2Agent(
        [
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id="ORD-001"),
            ),
            AnswerDecision(
                kind="answer",
                answer="不应生成这条结论。",
                evidence_ids=("order:ORD-001:shipped",),
            ),
        ]
    )
    graph, repository, engine, user_id, workspace_id, thread_id = _build_graph(
        tmp_path,
        agent,
        freshness_reader=_AlwaysStaleReader(),
    )
    preview = _start(
        graph,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )["l2_upgrade_preview"]

    result = _confirm(
        graph,
        preview.preview_id,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    case = repository.get_authorized_case(
        case_id=result["l2_runtime"].case_id,
        subject_id=user_id,
        user_id=user_id,
        workspace_id=workspace_id,
    )

    assert result["status"] == "l2_stopped"
    assert len(agent.requests) == 1
    assert repository.count_model_calls() == 1
    assert case is not None and case.failure_attribution == "context_stale"
    engine.dispose()


def test_model_unavailable_and_invalid_output_have_distinct_attribution(
    tmp_path: Path,
) -> None:
    """验证 Provider 不可用与结构化输出无效不会共用同一失败分类。"""

    attributions: list[str | None] = []
    for suffix, agent in (
        ("unavailable", ScriptedL2Agent([])),
        ("invalid", _InvalidOutputAgent()),
    ):
        scenario_root = tmp_path / suffix
        graph, repository, engine, user_id, workspace_id, thread_id = _build_graph(
            scenario_root,
            agent,
        )
        preview = _start(
            graph,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )["l2_upgrade_preview"]
        result = _confirm(
            graph,
            preview.preview_id,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
        case = repository.get_authorized_case(
            case_id=result["l2_runtime"].case_id,
            subject_id=user_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        assert case is not None
        attributions.append(case.failure_attribution)
        engine.dispose()

    assert attributions == ["model_unavailable", "model_output_invalid"]
