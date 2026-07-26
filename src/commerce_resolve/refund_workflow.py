"""为主图注册退款资格、审批、幂等执行和回读验证节点。"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import uuid4

from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import Dependencies, RefundGateway
from commerce_resolve.models import (
    PolicyFact,
    PolicyQuery,
    RefundEligibility,
)
from commerce_resolve.policy_rules import resolve_evidence_facts
from commerce_resolve.refund_rules import (
    REQUIRED_REFUND_FACT_IDS,
    assess_refund,
    build_facts_fingerprint,
    build_refund_preview,
)
from commerce_resolve.state import AgentState, RunContext

REFUND_INELIGIBLE_MESSAGES = {
    "refund_payment_missing": "该订单尚未配置可验证的 Mock 支付，请先在订单页面维护。",
    "refund_payment_not_settled": "该订单的 Mock 支付尚未结算，当前不能退款。",
    "refund_balance_zero": "该订单已无可退款余额。",
    "refund_requires_return_flow": (
        "该订单已发货或送达，需要先进入退货处理流程，当前没有创建退款。"
    ),
    "refund_window_expired": (
        "该订单已超过 7 天退货期限，当前不能直接退款。"
        "你仍可说明质量问题，我可以继续进行 AI 深度处理并创建 Mock Case。"
    ),
    "refund_conflict": "该订单已有待处理或已完成退款，不能重复申请。",
    "refund_business_facts_conflict": "订单与物流状态不一致，暂时不能安全退款。",
    "refund_policy_evidence_missing": "当前政策证据不足，不能生成可执行退款预览。",
}


def _scope(runtime: Runtime[RunContext]) -> BusinessScope:
    """从服务端运行上下文构造不可由客户端覆盖的业务作用域。"""

    return BusinessScope(
        user_id=runtime.context.user_id,
        workspace_id=runtime.context.workspace_id,
        access_mode=runtime.context.access_mode,
    )


def _refund_gateway(dependencies: Dependencies) -> RefundGateway:
    """返回已装配退款 Gateway，缺失时明确拒绝进入写流程。"""

    if dependencies.refund_gateway is None:
        raise ValueError("退款 Gateway 未装配")
    return dependencies.refund_gateway


def _refund_policy_facts(
    dependencies: Dependencies,
    *,
    as_of: date,
) -> tuple[tuple[PolicyFact, ...], str]:
    """解析退款授权事实及退货期限证据，供确定性规则按需使用。"""

    repository = dependencies.policy_repository
    if repository is None:
        return (), "unavailable"
    refund_result = repository.search(
        "发货前直接整单退款、原路退款与审核流程",
        PolicyQuery(
            topic="refund",
            aspects=("conditions", "method", "process"),
        ),
        as_of,
        limit=8,
    )
    return_result = repository.search(
        "普通商品签收后退货期限",
        PolicyQuery(
            topic="return",
            aspects=("window",),
        ),
        as_of,
        limit=4,
    )
    facts = resolve_evidence_facts(
        repository,
        refund_result.evidence_refs + return_result.evidence_refs,
    )
    facts_by_id = {fact.fact_id: fact for fact in facts}
    candidate_ids = REQUIRED_REFUND_FACT_IDS + ("return.window.general",)
    selected = tuple(
        facts_by_id[fact_id] for fact_id in candidate_ids if fact_id in facts_by_id
    )
    return (
        selected,
        f"{refund_result.corpus_version}:{refund_result.corpus_hash}",
    )


class RefundNodes:
    """封装依赖退款 Gateway 和政策仓库的主图节点。"""

    def __init__(self, dependencies: Dependencies) -> None:
        """保存本轮图使用的窄依赖，不持有请求身份。"""

        self._dependencies = dependencies

    def prepare_refund_request(self, state: AgentState) -> dict[str, object]:
        """清理上一轮退款结果，并保留已从多轮消息合并的订单和原因。"""

        return {
            "pending_refund_request": True,
            "refund_context": None,
            "refund_eligibility": None,
            "refund_action_id": None,
            "refund_preview": None,
            "refund_policy_fact_ids": (),
            "refund_result": None,
            "refund_verification": None,
            "error_code": None,
            "audit": ["refund_request_prepared"],
        }

    def request_refund_context(self, state: AgentState) -> dict[str, object]:
        """在订单号或退款原因缺失时明确请求补充并结束当前一轮。"""

        missing: list[str] = []
        if not state.get("order_id"):
            missing.append("订单号")
        if state.get("refund_reason") is None:
            missing.append("退款原因")
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": f"请补充{'和'.join(missing)}，我再为你核对退款资格。",
                }
            ],
            "status": "awaiting_refund_context",
            "audit": ["awaiting_refund_context"],
        }

    def load_refund_context(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """从业务 Gateway 读取当前作用域中的最新退款事实。"""

        order_id = state.get("order_id")
        if order_id is None:
            raise ValueError("读取退款上下文前缺少订单号")
        result = _refund_gateway(self._dependencies).get_refund_context(
            _scope(runtime),
            order_id,
        )
        if result.outcome != "found" or result.value is None:
            return {
                "status": "refund_ineligible",
                "error_code": "order_unavailable",
                "audit": ["refund_order_unavailable"],
            }
        return {
            "refund_context": result.value,
            "error_code": None,
            "audit": ["refund_context_loaded"],
        }

    def assess_refund(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """解析固定政策事实并用纯规则计算资格、余额和引用。"""

        context = state.get("refund_context")
        if context is None:
            return {
                "status": "refund_ineligible",
                "error_code": state.get("error_code") or "order_unavailable",
                "audit": ["refund_context_missing"],
            }
        try:
            facts, version = _refund_policy_facts(
                self._dependencies,
                as_of=runtime.context.as_of or date.today(),
            )
        except (LookupError, ValueError):
            facts, version = (), "unavailable"
        eligibility = assess_refund(context, facts)
        return {
            "refund_eligibility": eligibility,
            "refund_policy_fact_ids": eligibility.policy_fact_ids,
            "policy_citations": eligibility.citations,
            "policy_index_version": version,
            "error_code": None if eligibility.eligible else eligibility.reason_code,
            "audit": [f"refund_assessed:{eligibility.reason_code}"],
        }

    def respond_refund_ineligible(self, state: AgentState) -> dict[str, object]:
        """使用确定性原因解释不可退款，不触发任何退款写工具。"""

        code = state.get("error_code") or "refund_policy_evidence_missing"
        message = (
            "无法查询该订单，请检查订单号或当前账号。"
            if code == "order_unavailable"
            else REFUND_INELIGIBLE_MESSAGES.get(
                code,
                "当前业务事实不满足退款条件，未创建退款。",
            )
        )
        return {
            "messages": [{"role": "assistant", "content": message}],
            "pending_refund_request": False,
            "status": "refund_ineligible",
            "audit": ["responded:refund_ineligible"],
        }

    def build_refund_preview(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """纯构造稳定 action_id、业务指纹和 R2 预览，不写业务数据库。"""

        context = state.get("refund_context")
        eligibility = state.get("refund_eligibility")
        reason = state.get("refund_reason")
        task_id = runtime.context.task_id
        if context is None or eligibility is None or reason is None or task_id is None:
            raise ValueError("生成退款预览前缺少可信上下文")
        preview = build_refund_preview(
            action_id=str(uuid4()),
            task_id=task_id,
            reason=reason,
            context=context,
            eligibility=eligibility,
            policy_version=state.get("policy_index_version") or "unavailable",
        )
        return {
            "refund_action_id": preview.action_id,
            "refund_preview": preview,
            "audit": ["refund_preview_built"],
        }

    def reserve_refund_action(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """幂等保存待审批动作并生成面向用户的不可修改预览说明。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("保留退款动作前缺少预览")
        try:
            _refund_gateway(self._dependencies).reserve_preview(
                _scope(runtime),
                preview,
            )
        except (RuntimeError, ValueError) as error:
            code = str(error)
            return {
                "status": "refund_conflict",
                "error_code": code,
                "audit": [f"refund_reservation_failed:{code}"],
            }
        message = (
            f"退款预览：订单 {preview.order_id}，金额 ¥{preview.display_amount} "
            f"{preview.currency}，原路退回 {preview.channel}。"
            "这是 R2 Mock 资金动作，批准后才会写入本地退款记录。"
        )
        return {
            "messages": [{"role": "assistant", "content": message}],
            "status": "refund_awaiting_approval",
            "error_code": None,
            "audit": ["refund_action_reserved"],
        }

    def respond_refund_conflict(self, state: AgentState) -> dict[str, object]:
        """说明同订单已有冲突动作，不创建第二个待审批记录。"""

        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "该订单已有待处理或已完成退款，请先查看现有结果。",
                }
            ],
            "pending_refund_request": False,
            "status": "refund_conflict",
            "audit": ["responded:refund_conflict"],
        }

    def await_refund_approval(
        self,
        state: AgentState,
    ) -> Command[Literal["reject_refund_action", "revalidate_refund"]]:
        """暂停等待服务端绑定的明确决定，恢复时不执行前置副作用。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("等待退款审批前缺少预览")
        decision = interrupt(
            {
                "action_id": preview.action_id,
                "preview_hash": preview.preview_hash,
            }
        )
        if not isinstance(decision, dict):
            raise ValueError("退款审批决定格式无效")
        if decision.get("action_id") != preview.action_id:
            raise ValueError("退款审批动作不匹配")
        target = (
            "revalidate_refund"
            if decision.get("decision") == "approve"
            else "reject_refund_action"
            if decision.get("decision") == "reject"
            else None
        )
        if target is None:
            raise ValueError("退款审批决定无效")
        return Command(goto=target)

    def reject_refund_action(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """幂等持久化拒绝决定，保持退款副作用为零。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("拒绝退款前缺少预览")
        _refund_gateway(self._dependencies).reject_action(
            _scope(runtime),
            preview.task_id,
            preview.action_id,
            preview.preview_hash,
        )
        return {"audit": ["refund_action_rejected"]}

    def respond_refund_rejected(self, state: AgentState) -> dict[str, object]:
        """向用户确认已拒绝且未创建 Mock 退款。"""

        return {
            "messages": [
                {"role": "assistant", "content": "已拒绝本次退款，未创建退款记录。"}
            ],
            "pending_refund_request": False,
            "status": "refund_rejected",
            "audit": ["responded:refund_rejected"],
        }

    def revalidate_refund(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """批准后重新读取业务与政策事实，并检测旧预览是否失效。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("重新校验退款前缺少预览")
        context_result = _refund_gateway(self._dependencies).get_refund_context(
            _scope(runtime),
            preview.order_id,
        )
        if context_result.outcome != "found" or context_result.value is None:
            return {"error_code": "refund_preview_stale", "audit": ["refund_stale"]}
        try:
            facts, version = _refund_policy_facts(
                self._dependencies,
                as_of=runtime.context.as_of or date.today(),
            )
        except (LookupError, ValueError):
            return {"error_code": "refund_preview_stale", "audit": ["refund_stale"]}
        eligibility = assess_refund(context_result.value, facts)
        fingerprint = build_facts_fingerprint(
            context_result.value,
            policy_version=version,
            policy_fact_ids=eligibility.policy_fact_ids,
        )
        stale = (
            not eligibility.eligible
            or fingerprint != preview.facts_fingerprint
            or version != preview.policy_version
        )
        return {
            "refund_context": context_result.value,
            "refund_eligibility": eligibility,
            "error_code": "refund_preview_stale" if stale else None,
            "audit": ["refund_revalidated:stale" if stale else "refund_revalidated"],
        }

    def mark_refund_stale(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """在业务数据库中关闭已失效预览，防止旧批准继续执行。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("标记退款过期前缺少预览")
        _refund_gateway(self._dependencies).mark_stale(
            _scope(runtime),
            preview.task_id,
            preview.action_id,
            preview.preview_hash,
        )
        return {"audit": ["refund_action_marked_stale"]}

    def respond_refund_stale(self, state: AgentState) -> dict[str, object]:
        """说明预览已失效且需要重新发起，不声称退款成功。"""

        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "订单、支付或政策事实已变化，旧退款预览已失效，请重新申请。"
                    ),
                }
            ],
            "pending_refund_request": False,
            "status": "refund_preview_stale",
            "audit": ["responded:refund_preview_stale"],
        }

    def execute_refund(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """使用服务端预览中的稳定参数调用幂等 Mock Refund Gateway。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("执行退款前缺少预览")
        result = _refund_gateway(self._dependencies).execute_refund(
            _scope(runtime),
            preview.task_id,
            preview.action_id,
            preview.facts_fingerprint,
        )
        return {
            "refund_result": result,
            "error_code": None if result.outcome == "succeeded" else result.result_code,
            "audit": [f"refund_executed:{result.outcome}"],
        }

    def verify_refund(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """从独立业务读取验证退款结果，不复用执行返回值自证。"""

        preview = state.get("refund_preview")
        if preview is None:
            raise ValueError("验证退款前缺少预览")
        verification = _refund_gateway(self._dependencies).verify_refund(
            _scope(runtime),
            preview.action_id,
        )
        return {
            "refund_verification": verification,
            "error_code": None if verification.verified else verification.result_code,
            "audit": [f"refund_verified:{verification.result_code}"],
        }

    def respond_refund_completed(self, state: AgentState) -> dict[str, object]:
        """仅在业务回读验证通过后向用户声明 Mock 退款完成。"""

        verification = state.get("refund_verification")
        preview = state.get("refund_preview")
        if verification is None or not verification.verified or preview is None:
            raise ValueError("完成退款回复前缺少验证证据")
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"Mock 退款已完成并验证：{verification.refund_id}，"
                        f"金额 ¥{preview.display_amount} {preview.currency}。"
                    ),
                }
            ],
            "pending_refund_request": False,
            "status": "refund_completed",
            "audit": ["responded:refund_completed"],
        }

    def respond_refund_unknown(self, state: AgentState) -> dict[str, object]:
        """对结果未知场景保持保守状态，不自动再次创建退款。"""

        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "退款执行结果暂时未知，系统不会自动重复退款，请稍后核对。"
                    ),
                }
            ],
            "status": "refund_result_unknown",
            "audit": ["responded:refund_result_unknown"],
        }

    def respond_refund_failure(self, state: AgentState) -> dict[str, object]:
        """对业务拒绝、技术失败或验证不一致返回安全失败结果。"""

        verification = state.get("refund_verification")
        status = (
            "refund_verification_failed"
            if verification is not None and not verification.verified
            else "refund_failed"
        )
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "退款未能完成并验证，系统未将其标记为成功。",
                }
            ],
            "status": status,
            "audit": [f"responded:{status}"],
        }


def route_after_refund_details(
    state: AgentState,
) -> Literal["request_refund_context", "load_refund_context"]:
    """按订单号和退款原因是否齐全路由。"""

    return (
        "load_refund_context"
        if state.get("order_id") and state.get("refund_reason") is not None
        else "request_refund_context"
    )


def route_after_refund_context(
    state: AgentState,
) -> Literal["respond_refund_ineligible", "assess_refund"]:
    """订单不可访问时结束，否则进入政策与资格评估。"""

    return "respond_refund_ineligible" if state.get("error_code") else "assess_refund"


def route_after_refund_assessment(
    state: AgentState,
) -> Literal["respond_refund_ineligible", "build_refund_preview"]:
    """只有确定性资格通过时才生成 R2 预览。"""

    eligibility: RefundEligibility | None = state.get("refund_eligibility")
    return (
        "build_refund_preview"
        if eligibility is not None and eligibility.eligible
        else "respond_refund_ineligible"
    )


def route_after_refund_reservation(
    state: AgentState,
) -> Literal["respond_refund_conflict", "await_refund_approval"]:
    """业务约束冲突时结束，否则真正进入中断节点。"""

    return (
        "respond_refund_conflict"
        if state.get("error_code")
        else "await_refund_approval"
    )


def route_after_refund_revalidation(
    state: AgentState,
) -> Literal["mark_refund_stale", "execute_refund"]:
    """旧预览失效时禁止执行，否则进入幂等退款。"""

    return "mark_refund_stale" if state.get("error_code") else "execute_refund"


def route_after_refund_execution(
    state: AgentState,
) -> Literal["verify_refund", "respond_refund_unknown", "respond_refund_failure"]:
    """按 Gateway 有限结果选择回读、未知或失败路径。"""

    result = state.get("refund_result")
    if result is not None and result.outcome == "succeeded":
        return "verify_refund"
    if result is not None and result.outcome == "result_unknown":
        return "respond_refund_unknown"
    return "respond_refund_failure"


def route_after_refund_verification(
    state: AgentState,
) -> Literal["respond_refund_completed", "respond_refund_failure"]:
    """只有独立回读验证通过时才允许完成。"""

    verification = state.get("refund_verification")
    return (
        "respond_refund_completed"
        if verification is not None and verification.verified
        else "respond_refund_failure"
    )


def route_after_refund_terminal(
    state: AgentState,
) -> Literal["l2_record_refund_result", "__end__"]:
    """仅把活动 L2 发起的退款终态转换为 Observation，普通路径直接结束。"""

    l2_runtime = state.get("l2_runtime")
    return (
        "l2_record_refund_result"
        if l2_runtime is not None
        and l2_runtime.case_id is not None
        and l2_runtime.phase == "waiting_refund_approval"
        else END
    )


def register_refund_workflow(
    builder: StateGraph,
    dependencies: Dependencies,
) -> None:
    """把退款节点与边注册到现有唯一主图，不创建子图。"""

    nodes = RefundNodes(dependencies)
    builder.add_node("prepare_refund_request", nodes.prepare_refund_request)
    builder.add_node("request_refund_context", nodes.request_refund_context)
    builder.add_node("load_refund_context", nodes.load_refund_context)
    builder.add_node("assess_refund", nodes.assess_refund)
    builder.add_node("respond_refund_ineligible", nodes.respond_refund_ineligible)
    builder.add_node("build_refund_preview", nodes.build_refund_preview)
    builder.add_node("reserve_refund_action", nodes.reserve_refund_action)
    builder.add_node("respond_refund_conflict", nodes.respond_refund_conflict)
    builder.add_node("await_refund_approval", nodes.await_refund_approval)
    builder.add_node("reject_refund_action", nodes.reject_refund_action)
    builder.add_node("respond_refund_rejected", nodes.respond_refund_rejected)
    builder.add_node("revalidate_refund", nodes.revalidate_refund)
    builder.add_node("mark_refund_stale", nodes.mark_refund_stale)
    builder.add_node("respond_refund_stale", nodes.respond_refund_stale)
    builder.add_node("execute_refund", nodes.execute_refund)
    builder.add_node("verify_refund", nodes.verify_refund)
    builder.add_node("respond_refund_completed", nodes.respond_refund_completed)
    builder.add_node("respond_refund_unknown", nodes.respond_refund_unknown)
    builder.add_node("respond_refund_failure", nodes.respond_refund_failure)
    builder.add_conditional_edges("prepare_refund_request", route_after_refund_details)
    builder.add_conditional_edges("load_refund_context", route_after_refund_context)
    builder.add_conditional_edges("assess_refund", route_after_refund_assessment)
    builder.add_edge("build_refund_preview", "reserve_refund_action")
    builder.add_conditional_edges(
        "reserve_refund_action", route_after_refund_reservation
    )
    builder.add_edge("reject_refund_action", "respond_refund_rejected")
    builder.add_conditional_edges("revalidate_refund", route_after_refund_revalidation)
    builder.add_edge("mark_refund_stale", "respond_refund_stale")
    builder.add_conditional_edges("execute_refund", route_after_refund_execution)
    builder.add_conditional_edges("verify_refund", route_after_refund_verification)
    for terminal_node in (
        "request_refund_context",
        "respond_refund_ineligible",
        "respond_refund_conflict",
        "respond_refund_rejected",
        "respond_refund_stale",
        "respond_refund_completed",
        "respond_refund_unknown",
        "respond_refund_failure",
    ):
        if dependencies.l2 is None:
            builder.add_edge(terminal_node, END)
        else:
            builder.add_conditional_edges(terminal_node, route_after_refund_terminal)
