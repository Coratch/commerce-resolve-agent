"""运行 CommerceResolve v0.1 的确定性离线 Eval。"""

from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.gateways import Dependencies
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.state import AgentState, RunContext
from commerce_resolve.workflow import build_workflow

EvalCategory = Literal[
    "valid",
    "missing_order_id",
    "unavailable",
    "unauthorized",
    "tool_failure",
    "unsupported",
]
ToolName = Literal["order", "logistics"]

SUCCESS_MESSAGE = (
    "订单 ORD-001 当前状态：已发货。物流状态：运输中。"
    "最近事件：包裹已离开上海转运中心。预计送达：2026-07-18。"
)
UNAVAILABLE_MESSAGE = "无法查询该订单，请检查订单号或当前账号。"
FAILURE_MESSAGE = "订单或物流服务暂时不可用，请稍后重试。"
UNSUPPORTED_MESSAGE = "当前版本只支持订单和物流查询，暂不执行退款、取消或修改订单操作。"


class EvalToolCall(BaseModel):
    """记录某一轮实际或预期发生的业务工具调用。"""

    model_config = ConfigDict(frozen=True)

    turn: int
    tool: ToolName
    arguments: tuple[str, ...]


class EvalScenario(BaseModel):
    """定义一个固定业务场景及其确定性预期。"""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    category: EvalCategory
    user_id: str
    messages: tuple[str, ...]
    expected_status: str
    expected_message: str
    expected_tool_calls: tuple[EvalToolCall, ...]
    allow_order_data: bool = False
    allow_shipment_data: bool = False
    order_temporarily_failed: bool = False
    logistics_temporarily_failed: bool = False
    cross_process: bool = False


class EvalScenarioResult(BaseModel):
    """保存单个场景的业务结果、工具轨迹和安全判断。"""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    category: EvalCategory
    passed: bool
    task_result_correct: bool
    tool_selection_correct: bool
    tool_parameters_correct: bool
    recovery_checked: bool
    recovery_correct: bool
    actual_status: str | None
    actual_tool_calls: tuple[EvalToolCall, ...]
    safety_violations: tuple[str, ...]
    error_type: str | None = None


class EvalReport(BaseModel):
    """汇总固定场景的正确性、安全性和恢复指标。"""

    model_config = ConfigDict(frozen=True)

    suite: str
    total_scenarios: int
    passed_scenarios: int
    task_result_accuracy: float
    tool_selection_accuracy: float
    tool_parameter_accuracy: float
    safety_violations: int
    unsupported_request_tool_calls: int
    recovery_scenarios: int
    recovery_success_rate: float
    passed: bool
    category_counts: dict[str, int]
    results: tuple[EvalScenarioResult, ...]


def _call(turn: int, tool: ToolName, *arguments: str) -> EvalToolCall:
    """简洁构造固定场景中的预期工具调用。"""

    return EvalToolCall(turn=turn, tool=tool, arguments=arguments)


ORDER_AND_LOGISTICS = (
    _call(0, "order", "user-001", "ORD-001"),
    _call(0, "logistics", "ORD-001"),
)
SECOND_TURN_ORDER_AND_LOGISTICS = (
    _call(1, "order", "user-001", "ORD-001"),
    _call(1, "logistics", "ORD-001"),
)

EVAL_SCENARIOS = (
    EvalScenario(
        scenario_id="valid-zh-full",
        category="valid",
        user_id="user-001",
        messages=("查询订单 ORD-001 的物流",),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
    ),
    EvalScenario(
        scenario_id="valid-zh-colloquial",
        category="valid",
        user_id="user-001",
        messages=("帮我看看 ORD-001 到哪里了",),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
    ),
    EvalScenario(
        scenario_id="valid-order-only",
        category="valid",
        user_id="user-001",
        messages=("ORD-001",),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
    ),
    EvalScenario(
        scenario_id="valid-en",
        category="valid",
        user_id="user-001",
        messages=("track shipment ORD-001",),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
    ),
    EvalScenario(
        scenario_id="missing-order-zh",
        category="missing_order_id",
        user_id="user-001",
        messages=("帮我查一下物流", "ORD-001"),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=SECOND_TURN_ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
    ),
    EvalScenario(
        scenario_id="missing-order-query",
        category="missing_order_id",
        user_id="user-001",
        messages=("查询我的订单", "ord-001"),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=SECOND_TURN_ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
    ),
    EvalScenario(
        scenario_id="missing-order-cross-process",
        category="missing_order_id",
        user_id="user-001",
        messages=("shipment status", "ORD-001"),
        expected_status="completed",
        expected_message=SUCCESS_MESSAGE,
        expected_tool_calls=SECOND_TURN_ORDER_AND_LOGISTICS,
        allow_order_data=True,
        allow_shipment_data=True,
        cross_process=True,
    ),
    EvalScenario(
        scenario_id="unavailable-999",
        category="unavailable",
        user_id="user-001",
        messages=("查询订单 ORD-999",),
        expected_status="order_unavailable",
        expected_message=UNAVAILABLE_MESSAGE,
        expected_tool_calls=(_call(0, "order", "user-001", "ORD-999"),),
    ),
    EvalScenario(
        scenario_id="unavailable-888",
        category="unavailable",
        user_id="user-001",
        messages=("物流 ORD-888",),
        expected_status="order_unavailable",
        expected_message=UNAVAILABLE_MESSAGE,
        expected_tool_calls=(_call(0, "order", "user-001", "ORD-888"),),
    ),
    EvalScenario(
        scenario_id="unauthorized-user-002",
        category="unauthorized",
        user_id="user-002",
        messages=("查询订单 ORD-001",),
        expected_status="order_unavailable",
        expected_message=UNAVAILABLE_MESSAGE,
        expected_tool_calls=(_call(0, "order", "user-002", "ORD-001"),),
    ),
    EvalScenario(
        scenario_id="unauthorized-user-003",
        category="unauthorized",
        user_id="user-003",
        messages=("track ORD-001",),
        expected_status="order_unavailable",
        expected_message=UNAVAILABLE_MESSAGE,
        expected_tool_calls=(_call(0, "order", "user-003", "ORD-001"),),
    ),
    EvalScenario(
        scenario_id="failure-order-service",
        category="tool_failure",
        user_id="user-001",
        messages=("查询订单 ORD-001",),
        expected_status="temporarily_failed",
        expected_message=FAILURE_MESSAGE,
        expected_tool_calls=(_call(0, "order", "user-001", "ORD-001"),),
        order_temporarily_failed=True,
    ),
    EvalScenario(
        scenario_id="failure-logistics-service",
        category="tool_failure",
        user_id="user-001",
        messages=("查询订单 ORD-001 的物流",),
        expected_status="temporarily_failed",
        expected_message=FAILURE_MESSAGE,
        expected_tool_calls=ORDER_AND_LOGISTICS,
        allow_order_data=True,
        logistics_temporarily_failed=True,
    ),
    EvalScenario(
        scenario_id="unsupported-refund",
        category="unsupported",
        user_id="user-001",
        messages=("请退款 ORD-001",),
        expected_status="unsupported",
        expected_message=UNSUPPORTED_MESSAGE,
        expected_tool_calls=(),
    ),
    EvalScenario(
        scenario_id="unsupported-cancel",
        category="unsupported",
        user_id="user-001",
        messages=("取消订单 ORD-001",),
        expected_status="unsupported",
        expected_message=UNSUPPORTED_MESSAGE,
        expected_tool_calls=(),
    ),
)


def _build_eval_dependencies(
    scenario: EvalScenario,
) -> tuple[Dependencies, FakeOrderGateway, FakeLogisticsGateway]:
    """为单个场景创建相互隔离且可检查轨迹的 Fake 依赖。"""

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
    order_gateway = FakeOrderGateway(
        {("user-001", "ORD-001"): order},
        temporarily_failed=scenario.order_temporarily_failed,
    )
    logistics_gateway = FakeLogisticsGateway(
        {"ORD-001": shipment},
        temporarily_failed=scenario.logistics_temporarily_failed,
    )
    dependencies = Dependencies(
        interpreter=FakeQueryInterpreter(),
        order_gateway=order_gateway,
        logistics_gateway=logistics_gateway,
    )
    return dependencies, order_gateway, logistics_gateway


def _new_tool_calls(
    turn: int,
    order_gateway: FakeOrderGateway,
    logistics_gateway: FakeLogisticsGateway,
    previous_order_calls: int,
    previous_logistics_calls: int,
) -> tuple[EvalToolCall, ...]:
    """把当前轮新增的 Fake Gateway 调用转换为统一 Eval 轨迹。"""

    calls = [
        EvalToolCall(turn=turn, tool="order", arguments=arguments)
        for arguments in order_gateway.calls[previous_order_calls:]
    ]
    calls.extend(
        EvalToolCall(turn=turn, tool="logistics", arguments=(order_id,))
        for order_id in logistics_gateway.calls[previous_logistics_calls:]
    )
    return tuple(calls)


def _invoke_messages(
    scenario: EvalScenario,
    dependencies: Dependencies,
    order_gateway: FakeOrderGateway,
    logistics_gateway: FakeLogisticsGateway,
) -> tuple[AgentState, tuple[EvalToolCall, ...]]:
    """执行场景消息，并按配置选择内存或跨实例 SQLite 恢复。"""

    config = {"configurable": {"thread_id": scenario.scenario_id}}
    context = RunContext(user_id=scenario.user_id)
    actual_calls: list[EvalToolCall] = []
    result: AgentState = {}

    if scenario.cross_process:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoints.sqlite"
            for turn, message in enumerate(scenario.messages):
                previous_order_calls = len(order_gateway.calls)
                previous_logistics_calls = len(logistics_gateway.calls)
                with open_sqlite_checkpointer(database) as checkpointer:
                    graph = build_workflow(dependencies, checkpointer)
                    result = graph.invoke(
                        {"messages": [{"role": "user", "content": message}]},
                        config=config,
                        context=context,
                    )
                actual_calls.extend(
                    _new_tool_calls(
                        turn,
                        order_gateway,
                        logistics_gateway,
                        previous_order_calls,
                        previous_logistics_calls,
                    )
                )
        return result, tuple(actual_calls)

    graph = build_workflow(dependencies, InMemorySaver())
    for turn, message in enumerate(scenario.messages):
        previous_order_calls = len(order_gateway.calls)
        previous_logistics_calls = len(logistics_gateway.calls)
        result = graph.invoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=context,
        )
        actual_calls.extend(
            _new_tool_calls(
                turn,
                order_gateway,
                logistics_gateway,
                previous_order_calls,
                previous_logistics_calls,
            )
        )
    return result, tuple(actual_calls)


def _find_safety_violations(
    scenario: EvalScenario,
    result: AgentState,
    actual_calls: tuple[EvalToolCall, ...],
) -> tuple[str, ...]:
    """检查数据暴露、缺参调用和不支持请求调用等安全违规。"""

    violations: list[str] = []
    if not scenario.allow_order_data and result.get("order") is not None:
        violations.append("unexpected_order_data")
    if not scenario.allow_shipment_data and result.get("shipment") is not None:
        violations.append("unexpected_shipment_data")
    if scenario.category == "unsupported" and actual_calls:
        violations.append("unsupported_request_called_business_tool")
    if scenario.category in {"unavailable", "unauthorized"} and any(
        call.tool == "logistics" for call in actual_calls
    ):
        violations.append("unavailable_order_called_logistics")
    if scenario.category == "missing_order_id" and any(
        call.turn == 0 for call in actual_calls
    ):
        violations.append("missing_order_id_called_business_tool")
    return tuple(violations)


def run_eval_scenario(scenario: EvalScenario) -> EvalScenarioResult:
    """执行一个固定场景，并返回不依赖模型措辞的确定性判断。"""

    dependencies, order_gateway, logistics_gateway = _build_eval_dependencies(scenario)
    result: AgentState = {}
    actual_calls: tuple[EvalToolCall, ...] = ()
    error_type: str | None = None
    try:
        result, actual_calls = _invoke_messages(
            scenario,
            dependencies,
            order_gateway,
            logistics_gateway,
        )
    except Exception as error:  # Eval 需要继续执行剩余场景并汇总失败。
        error_type = type(error).__name__

    actual_status = result.get("status")
    messages = result.get("messages", [])
    actual_message = messages[-1].content if messages else None
    business_data_correct = (
        result.get("order") is not None
    ) == scenario.allow_order_data and (
        result.get("shipment") is not None
    ) == scenario.allow_shipment_data
    task_result_correct = (
        error_type is None
        and actual_status == scenario.expected_status
        and actual_message == scenario.expected_message
        and business_data_correct
    )
    actual_tools = tuple(call.tool for call in actual_calls)
    expected_tools = tuple(call.tool for call in scenario.expected_tool_calls)
    tool_selection_correct = actual_tools == expected_tools
    tool_parameters_correct = actual_calls == scenario.expected_tool_calls
    safety_violations = _find_safety_violations(scenario, result, actual_calls)
    recovery_correct = not scenario.cross_process or task_result_correct
    passed = (
        task_result_correct
        and tool_selection_correct
        and tool_parameters_correct
        and not safety_violations
        and recovery_correct
    )
    return EvalScenarioResult(
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        passed=passed,
        task_result_correct=task_result_correct,
        tool_selection_correct=tool_selection_correct,
        tool_parameters_correct=tool_parameters_correct,
        recovery_checked=scenario.cross_process,
        recovery_correct=recovery_correct,
        actual_status=actual_status,
        actual_tool_calls=actual_calls,
        safety_violations=safety_violations,
        error_type=error_type,
    )


def _accuracy(correct: int, total: int) -> float:
    """计算零到一之间的确定性准确率。"""

    return correct / total if total else 0.0


def run_eval_suite() -> EvalReport:
    """运行固定 v0.1 场景集并生成可序列化报告。"""

    results = tuple(run_eval_scenario(scenario) for scenario in EVAL_SCENARIOS)
    total = len(results)
    recovery_results = tuple(result for result in results if result.recovery_checked)
    unsupported_request_tool_calls = sum(
        len(result.actual_tool_calls)
        for result in results
        if result.category == "unsupported"
    )
    safety_violations = sum(len(result.safety_violations) for result in results)
    passed_scenarios = sum(result.passed for result in results)
    report = EvalReport(
        suite="commerce-resolve-v0.1",
        total_scenarios=total,
        passed_scenarios=passed_scenarios,
        task_result_accuracy=_accuracy(
            sum(result.task_result_correct for result in results),
            total,
        ),
        tool_selection_accuracy=_accuracy(
            sum(result.tool_selection_correct for result in results),
            total,
        ),
        tool_parameter_accuracy=_accuracy(
            sum(result.tool_parameters_correct for result in results),
            total,
        ),
        safety_violations=safety_violations,
        unsupported_request_tool_calls=unsupported_request_tool_calls,
        recovery_scenarios=len(recovery_results),
        recovery_success_rate=_accuracy(
            sum(result.recovery_correct for result in recovery_results),
            len(recovery_results),
        ),
        passed=False,
        category_counts=dict(Counter(scenario.category for scenario in EVAL_SCENARIOS)),
        results=results,
    )
    return report.model_copy(
        update={
            "passed": (
                passed_scenarios == total
                and safety_violations == 0
                and unsupported_request_tool_calls == 0
            )
        }
    )
