"""编排订单查询、政策 RAG 和多轮条件补充的 LangGraph 主图。"""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import Dependencies
from commerce_resolve.l2_models import L2RuntimeState
from commerce_resolve.l2_workflow import register_l2_workflow
from commerce_resolve.models import InterpretationContext, OrderView, ShipmentView
from commerce_resolve.policy_workflow import register_policy_workflow
from commerce_resolve.refund_workflow import register_refund_workflow
from commerce_resolve.state import AgentState, RunContext

ORDER_STATUS_LABELS = {
    "processing": "处理中",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}
SHIPMENT_STATUS_LABELS = {
    "preparing": "待揽收",
    "in_transit": "运输中",
    "delivered": "已签收",
}
ORDER_UNAVAILABLE_MESSAGE = "无法查询该订单，请检查订单号或当前账号。"
TEMPORARILY_FAILED_MESSAGE = "订单或物流服务暂时不可用，请稍后重试。"
UNSUPPORTED_WRITE_MESSAGE = (
    "当前版本只支持订单和物流查询，暂不执行退款、取消或修改订单操作。"
)


def _latest_user_text(state: AgentState) -> str:
    """读取最新用户文本，并拒绝 v0.1 尚未支持的非文本内容。"""

    content = state["messages"][-1].content
    if not isinstance(content, str):
        raise ValueError("T1 only supports text input")
    return content


def _format_success(order: OrderView, shipment: ShipmentView) -> str:
    """将经过业务工具验证的订单和物流事实格式化为中文回复。"""

    estimated_delivery = (
        shipment.estimated_delivery_at.isoformat()
        if shipment.estimated_delivery_at is not None
        else "未知"
    )
    return (
        f"订单 {order.order_id} 当前状态：{ORDER_STATUS_LABELS[order.status]}。"
        f"物流状态：{SHIPMENT_STATUS_LABELS[shipment.status]}。"
        f"最近事件：{shipment.last_event}。"
        f"预计送达：{estimated_delivery}。"
    )


def build_workflow(
    dependencies: Dependencies,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
):
    """使用注入能力构建订单、政策、退款和可选 L2 的唯一主图。"""

    def bind_and_interpret(
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """绑定当前用户，并从最新消息提取订单或政策查询意图。"""

        owner_user_id = state.get("owner_user_id")
        if owner_user_id is not None and owner_user_id != runtime.context.user_id:
            raise ValueError("无法继续该会话，请检查当前账号")
        owner_workspace_id = state.get("owner_workspace_id")
        if (
            owner_workspace_id is not None
            and owner_workspace_id != runtime.context.workspace_id
        ):
            raise ValueError("无法继续该会话，请检查当前工作区")
        previous_policy_query = state.get("pending_policy_query") or state.get(
            "policy_query"
        )
        pending_refund_request = state.get("pending_refund_request", False)
        interpretation_context = InterpretationContext(
            previous_policy_query=previous_policy_query,
            pending_refund_request=pending_refund_request,
        )
        interpretation = dependencies.interpreter.interpret(
            _latest_user_text(state),
            interpretation_context,
        )
        interpreted_intent = (
            "unsupported_write"
            if (
                interpretation.intent == "refund_request"
                and dependencies.refund_gateway is None
            )
            or (
                interpretation.intent == "l2_support_request"
                and dependencies.l2 is None
            )
            else interpretation.intent
        )
        continuing_order_inquiry = (
            state.get("status") == "awaiting_order_id"
            and state.get("intent") == "order_inquiry"
            and interpreted_intent == "order_inquiry"
            and interpretation.order_id is not None
        )
        continuing_refund_request = bool(
            pending_refund_request and interpreted_intent == "refund_request"
        )
        intent = (
            "refund_request"
            if continuing_refund_request
            else state["intent"]
            if continuing_order_inquiry
            else interpreted_intent
        )
        if intent not in {
            "order_inquiry",
            "policy_inquiry",
            "refund_request",
            "l2_support_request",
            "unsupported_write",
        }:
            raise ValueError("对不起，当前无法识别您的意图，请咨询和订单相关的问题")
        return {
            "owner_user_id": owner_user_id or runtime.context.user_id,
            "owner_workspace_id": (owner_workspace_id or runtime.context.workspace_id),
            "intent": intent,
            "order_id": (
                interpretation.order_id or state.get("order_id")
                if continuing_refund_request
                else interpretation.order_id
            ),
            "order": None,
            "shipment": None,
            "policy_query": interpretation.policy_query,
            "pending_policy_query": None,
            "policy_evidence_refs": (),
            "selected_policy_fact_ids": (),
            "policy_citations": (),
            "policy_conflicts": (),
            "missing_policy_dimensions": (),
            "policy_index_version": None,
            "refund_reason": (
                interpretation.refund_reason or state.get("refund_reason")
                if continuing_refund_request
                else interpretation.refund_reason
            ),
            "l2_upgrade_preview": None,
            "l2_runtime": (
                L2RuntimeState(
                    phase="awaiting_confirmation",
                    issue_summary=interpretation.l2_issue_summary,
                    related_order_id=interpretation.order_id,
                    latest_user_input=interpretation.l2_issue_summary,
                )
                if intent == "l2_support_request"
                and interpretation.l2_issue_summary is not None
                else None
            ),
            "error_code": None,
            "audit": [f"interpreted:{intent}"],
        }

    def route_after_interpret(
        state: AgentState,
    ) -> Literal[
        "respond_unsupported",
        "prepare_policy_query",
        "prepare_refund_request",
        "l2_prepare_upgrade",
        "request_order_id",
        "query_order",
    ]:
        """根据结构化意图和订单号选择安全处理路径。"""

        if state["intent"] == "unsupported_write":
            return "respond_unsupported"
        if state["intent"] == "policy_inquiry":
            return "prepare_policy_query"
        if state["intent"] == "refund_request":
            return "prepare_refund_request"
        if state["intent"] == "l2_support_request":
            return "l2_prepare_upgrade"
        return "query_order" if state.get("order_id") else "request_order_id"

    def request_order_id(state: AgentState) -> dict[str, object]:
        """请求用户补充订单号，并正常结束当前一轮。"""

        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "请提供需要查询的订单号。",
                }
            ],
            "status": "awaiting_order_id",
            "audit": ["awaiting_order_id"],
        }

    def query_order(
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """使用当前用户身份查询订单，拒绝不可用或未授权订单。"""

        order_id = state.get("order_id")
        if order_id is None:
            raise ValueError("查询订单前必须提供订单号")
        scope = BusinessScope(
            user_id=runtime.context.user_id,
            workspace_id=runtime.context.workspace_id,
            access_mode=runtime.context.access_mode,
        )
        result = dependencies.order_gateway.get_order(
            scope,
            order_id,
        )
        if result.outcome == "unavailable":
            return {
                "status": "order_unavailable",
                "error_code": "order_unavailable",
                "audit": ["order_unavailable"],
            }
        if result.outcome == "temporarily_failed":
            return {
                "status": "temporarily_failed",
                "error_code": "order_temporarily_failed",
                "audit": ["order_temporarily_failed"],
            }
        if result.value is None:
            raise ValueError("订单工具返回了无效结果")
        return {
            "order": result.value,
            "error_code": None,
            "audit": ["order_queried"],
        }

    def route_after_order(
        state: AgentState,
    ) -> Literal["respond_unavailable", "respond_failure", "query_shipment"]:
        """根据订单工具结果选择失败回复或继续查询物流。"""

        if state.get("error_code") == "order_unavailable":
            return "respond_unavailable"
        if state.get("error_code") == "order_temporarily_failed":
            return "respond_failure"
        return "query_shipment"

    def query_shipment(
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """使用完整可信作用域重新验证并查询物流信息。"""

        order_id = state.get("order_id")
        if order_id is None:
            raise ValueError("查询物流前必须提供订单号")
        scope = BusinessScope(
            user_id=runtime.context.user_id,
            workspace_id=runtime.context.workspace_id,
            access_mode=runtime.context.access_mode,
        )
        result = dependencies.logistics_gateway.get_shipment(scope, order_id)
        if result.outcome in {"unavailable", "temporarily_failed"}:
            error_code = (
                "shipment_unavailable"
                if result.outcome == "unavailable"
                else "shipment_temporarily_failed"
            )
            return {
                "status": "temporarily_failed",
                "error_code": error_code,
                "audit": [error_code],
            }
        if result.value is None:
            raise ValueError("物流工具返回了无效结果")
        return {
            "shipment": result.value,
            "error_code": None,
            "audit": ["shipment_queried"],
        }

    def route_after_shipment(
        state: AgentState,
    ) -> Literal["respond_failure", "respond_success"]:
        """根据物流工具结果选择失败回复或成功回复。"""

        return "respond_failure" if state.get("error_code") else "respond_success"

    def respond_unavailable(state: AgentState) -> dict[str, object]:
        """使用统一消息回复不存在或未授权订单。"""

        return {
            "messages": [{"role": "assistant", "content": ORDER_UNAVAILABLE_MESSAGE}],
            "status": "order_unavailable",
            "error_code": "order_unavailable",
            "audit": ["responded:order_unavailable"],
        }

    def respond_failure(state: AgentState) -> dict[str, object]:
        """使用脱敏消息回复业务工具暂时失败。"""

        return {
            "messages": [{"role": "assistant", "content": TEMPORARILY_FAILED_MESSAGE}],
            "status": "temporarily_failed",
            "audit": ["responded:temporarily_failed"],
        }

    def respond_unsupported(state: AgentState) -> dict[str, object]:
        """明确拒绝 v0.1 尚未支持的业务写操作。"""

        return {
            "messages": [{"role": "assistant", "content": UNSUPPORTED_WRITE_MESSAGE}],
            "status": "unsupported",
            "error_code": "unsupported_write",
            "audit": ["responded:unsupported_write"],
        }

    def respond_success(state: AgentState) -> dict[str, object]:
        """根据结构化业务事实生成成功回复并结束任务。"""

        order = state.get("order")
        shipment = state.get("shipment")
        if order is None or shipment is None:
            raise ValueError("生成成功回复前缺少订单或物流结果")
        response = _format_success(order, shipment)
        return {
            "messages": [{"role": "assistant", "content": response}],
            "status": "completed",
            "error_code": None,
            "audit": ["completed"],
        }

    builder = StateGraph(AgentState, context_schema=RunContext)
    builder.add_node("bind_and_interpret", bind_and_interpret)
    builder.add_node("request_order_id", request_order_id)
    builder.add_node("query_order", query_order)
    builder.add_node("query_shipment", query_shipment)
    builder.add_node("respond_unavailable", respond_unavailable)
    builder.add_node("respond_failure", respond_failure)
    builder.add_node("respond_unsupported", respond_unsupported)
    builder.add_node("respond_success", respond_success)
    register_policy_workflow(builder, dependencies)
    register_refund_workflow(builder, dependencies)
    if dependencies.l2 is not None:
        register_l2_workflow(builder, dependencies)
    else:

        def reject_unconfigured_l2(state: AgentState) -> dict[str, object]:
            """为无 L2 依赖的兼容图提供永不实际命中的安全终点。"""

            return respond_unsupported(state)

        builder.add_node("l2_prepare_upgrade", reject_unconfigured_l2)
        builder.add_edge("l2_prepare_upgrade", END)
    builder.add_edge(START, "bind_and_interpret")
    builder.add_conditional_edges("bind_and_interpret", route_after_interpret)
    builder.add_edge("request_order_id", END)
    builder.add_conditional_edges("query_order", route_after_order)
    builder.add_conditional_edges("query_shipment", route_after_shipment)
    builder.add_edge("respond_unavailable", END)
    builder.add_edge("respond_failure", END)
    builder.add_edge("respond_unsupported", END)
    builder.add_edge("respond_success", END)
    return builder.compile(checkpointer=checkpointer, store=store)
