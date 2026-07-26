"""在单 Agent 主图中编排订单、物流与政策的组合只读咨询。"""

from __future__ import annotations

from datetime import date

from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import (
    Dependencies,
    PolicyRepositoryUnavailableError,
)
from commerce_resolve.policy_rules import (
    detect_policy_conflicts,
    find_missing_dimensions,
    resolve_evidence_facts,
    select_applicable_facts,
    unsupported_aspects,
)
from commerce_resolve.service_resolution import (
    ServiceProgressStep,
    ServiceResolution,
    ServiceVerifiedFact,
)
from commerce_resolve.state import AgentState, RunContext

ORDER_LABELS = {
    "processing": "处理中",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}
SHIPMENT_LABELS = {
    "preparing": "待揽收",
    "in_transit": "运输中",
    "delivered": "已签收",
}


def _scope(runtime: Runtime[RunContext]) -> BusinessScope:
    """从可信运行上下文构造只读业务作用域。"""

    return BusinessScope(
        user_id=runtime.context.user_id,
        workspace_id=runtime.context.workspace_id,
        access_mode=runtime.context.access_mode,
    )


def _latest_user_text(state: AgentState) -> str:
    """读取组合咨询的最新用户文本。"""

    content = state["messages"][-1].content
    if not isinstance(content, str):
        raise ValueError("组合咨询只支持文本输入")
    return content


class ServiceGuidanceNodes:
    """封装组合咨询所需的单次只读调用和确定性方案装配。"""

    def __init__(self, dependencies: Dependencies) -> None:
        """保存注入依赖，不在节点外缓存客户业务数据。"""

        self._dependencies = dependencies

    def prepare_service_guidance(self, state: AgentState) -> dict[str, object]:
        """验证目标和关注点，并清空上一轮组合方案。"""

        concerns = state.get("service_concerns", ())
        if len(set(concerns)) < 2:
            raise ValueError("组合咨询至少需要两个关注点")
        return {
            "service_resolution": None,
            "guidance_policy_claims": (),
            "error_code": (
                None if state.get("order_id") is not None else "guidance_missing_order"
            ),
            "audit": ["service_guidance_prepared"],
        }

    def load_guidance_order(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """最多调用一次订单 Gateway，并保存结构化结果。"""

        order_id = state.get("order_id")
        if order_id is None:
            return {"audit": ["service_guidance_order_skipped"]}
        result = self._dependencies.order_gateway.get_order(_scope(runtime), order_id)
        if result.outcome == "unavailable":
            return {
                "error_code": "guidance_order_unavailable",
                "audit": ["service_guidance_order_unavailable"],
            }
        if result.outcome == "temporarily_failed":
            return {
                "error_code": "guidance_order_failed",
                "audit": ["service_guidance_order_failed"],
            }
        return {
            "order": result.value,
            "error_code": None,
            "audit": ["service_guidance_order_loaded"],
        }

    def load_guidance_shipment(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """仅在需要且订单可用时调用一次物流 Gateway。"""

        if "shipment_status" not in state.get("service_concerns", ()):
            return {"audit": ["service_guidance_shipment_skipped"]}
        if state.get("order") is None or state.get("order_id") is None:
            return {"audit": ["service_guidance_shipment_blocked"]}
        result = self._dependencies.logistics_gateway.get_shipment(
            _scope(runtime),
            state["order_id"],
        )
        if result.outcome == "unavailable":
            return {
                "error_code": "guidance_shipment_unavailable",
                "audit": ["service_guidance_shipment_unavailable"],
            }
        if result.outcome == "temporarily_failed":
            return {
                "error_code": "guidance_shipment_failed",
                "audit": ["service_guidance_shipment_failed"],
            }
        return {
            "shipment": result.value,
            "error_code": None,
            "audit": ["service_guidance_shipment_loaded"],
        }

    def retrieve_guidance_policy(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """需要政策时只执行一次检索，不让模型补写缺失证据。"""

        concerns = set(state.get("service_concerns", ()))
        if not concerns.intersection({"policy", "refund_eligibility"}):
            return {"audit": ["service_guidance_policy_skipped"]}
        query = state.get("policy_query")
        if query is None or self._dependencies.policy_repository is None:
            return {
                "error_code": "guidance_policy_unavailable",
                "audit": ["service_guidance_policy_unavailable"],
            }
        try:
            result = self._dependencies.policy_repository.search(
                _latest_user_text(state),
                query,
                runtime.context.as_of or date.today(),
            )
        except PolicyRepositoryUnavailableError:
            return {
                "error_code": "guidance_policy_failed",
                "audit": ["service_guidance_policy_failed"],
            }
        return {
            "policy_evidence_refs": result.evidence_refs,
            "policy_index_version": f"{result.corpus_version}:{result.corpus_hash}",
            "error_code": (
                state["error_code"]
                if state.get("error_code") is not None
                else None
                if result.evidence_refs
                else "guidance_policy_insufficient"
            ),
            "audit": ["service_guidance_policy_retrieved"],
        }

    def assess_guidance_evidence(self, state: AgentState) -> dict[str, object]:
        """解析一次检索证据并确定缺失条件、冲突、结论和引用。"""

        concerns = set(state.get("service_concerns", ()))
        if not concerns.intersection({"policy", "refund_eligibility"}):
            return {"audit": ["service_guidance_evidence_skipped"]}
        query = state.get("policy_query")
        repository = self._dependencies.policy_repository
        if query is None or repository is None or not state.get("policy_evidence_refs"):
            return {"audit": ["service_guidance_evidence_missing"]}
        try:
            facts = resolve_evidence_facts(
                repository,
                state.get("policy_evidence_refs", ()),
            )
        except PolicyRepositoryUnavailableError:
            return {
                "error_code": "guidance_policy_failed",
                "audit": ["service_guidance_evidence_failed"],
            }
        missing = find_missing_dimensions(query, facts)
        selected = select_applicable_facts(query, facts)
        conflicts = detect_policy_conflicts(selected) if not missing else ()
        if missing:
            return {
                "missing_policy_dimensions": missing,
                "audit": ["service_guidance_policy_context_missing"],
            }
        if conflicts:
            return {
                "policy_conflicts": conflicts,
                "audit": ["service_guidance_policy_conflict"],
            }
        if not selected or unsupported_aspects(query, selected):
            return {
                "error_code": "guidance_policy_insufficient",
                "audit": ["service_guidance_policy_insufficient"],
            }
        return {
            "selected_policy_fact_ids": tuple(item.fact_id for item in selected),
            "policy_citations": tuple(item.citation for item in selected),
            "guidance_policy_claims": tuple(item.claim_text for item in selected),
            "error_code": state.get("error_code"),
            "audit": ["service_guidance_evidence_assessed"],
        }

    def assemble_service_resolution(self, state: AgentState) -> dict[str, object]:
        """只从已验证 State 生成方案、停止原因和允许动作。"""

        order = state.get("order")
        shipment = state.get("shipment")
        concerns = set(state.get("service_concerns", ()))
        verified: list[ServiceVerifiedFact] = []
        missing: list[str] = []
        progress: list[ServiceProgressStep] = []
        if order is not None:
            verified.append(
                ServiceVerifiedFact(
                    category="order",
                    statement=(
                        f"订单 {order.order_id} 当前状态为"
                        f"{ORDER_LABELS[order.status]}。"
                    ),
                    evidence_id=f"order:{order.order_id}",
                )
            )
            progress.append(
                ServiceProgressStep(
                    key="order",
                    title="已核对订单",
                    state="completed",
                )
            )
        else:
            missing.append("可访问的订单号或订单事实")
            progress.append(
                ServiceProgressStep(
                    key="order",
                    title="订单事实未取得",
                    state="blocked",
                )
            )
        if "shipment_status" in concerns:
            if shipment is not None:
                verified.append(
                    ServiceVerifiedFact(
                        category="shipment",
                        statement=(
                            f"物流状态为{SHIPMENT_LABELS[shipment.status]}；"
                            f"最新进展：{shipment.last_event}。"
                        ),
                        evidence_id=f"shipment:{shipment.order_id}",
                    )
                )
                progress.append(
                    ServiceProgressStep(
                        key="shipment",
                        title="已核对物流",
                        state="completed",
                    )
                )
            else:
                missing.append("当前物流事实")
                progress.append(
                    ServiceProgressStep(
                        key="shipment",
                        title="物流事实未取得",
                        state="blocked",
                    )
                )
        claims = state.get("guidance_policy_claims", ())
        for fact_id, claim in zip(
            state.get("selected_policy_fact_ids", ()),
            claims,
            strict=True,
        ):
            verified.append(
                ServiceVerifiedFact(
                    category="policy",
                    statement=claim,
                    evidence_id=f"policy:{fact_id}",
                )
            )
        if concerns.intersection({"policy", "refund_eligibility"}):
            if claims:
                progress.append(
                    ServiceProgressStep(
                        key="policy",
                        title="已核对政策",
                        state="completed",
                    )
                )
            else:
                missing.append("足以判断的当前政策证据")
                progress.append(
                    ServiceProgressStep(
                        key="policy",
                        title="政策证据不完整",
                        state="blocked",
                    )
                )
        missing_dimensions = state.get("missing_policy_dimensions", ())
        if missing_dimensions:
            missing.append("商品类别或拆封状态")
        error = state.get("error_code")
        if state.get("order_id") is None or error == "guidance_missing_order":
            stop_reason = "needs_user_input"
        elif error == "guidance_order_unavailable":
            stop_reason = "order_unavailable"
        elif error in {
            "guidance_order_failed",
            "guidance_shipment_failed",
            "guidance_policy_failed",
        }:
            stop_reason = "tool_failed"
        elif state.get("policy_conflicts"):
            stop_reason = "conflicting_evidence"
        elif missing_dimensions:
            stop_reason = "needs_user_input"
        elif error in {
            "guidance_shipment_unavailable",
            "guidance_policy_unavailable",
            "guidance_policy_insufficient",
        }:
            stop_reason = "insufficient_evidence"
        else:
            stop_reason = "completed"
        allowed_actions = []
        if state.get("order_id") is not None:
            allowed_actions.append("view_order")
        if concerns.intersection({"policy", "refund_eligibility"}):
            allowed_actions.append("view_policy")
        if (
            "refund_eligibility" in concerns
            and order is not None
            and order.status == "delivered"
            and claims
        ):
            allowed_actions.append("request_refund")
        if stop_reason in {"needs_user_input", "insufficient_evidence"}:
            allowed_actions.append("provide_information")
        if stop_reason in {"tool_failed", "conflicting_evidence"}:
            allowed_actions.append("upgrade_l2")
        recommendations = ["先根据已验证事实处理；缺失信息不会由模型猜测。"]
        if "refund_eligibility" in concerns:
            recommendations.append(
                "政策咨询不会创建退款；如需办理，请单独发起退款申请并重新核验支付与资格。"
            )
        next_step = {
            "completed": "查看建议并选择允许的下一步操作。",
            "needs_user_input": "补充缺失信息后再次咨询。",
            "insufficient_evidence": "查看订单或政策详情，必要时补充信息。",
            "conflicting_evidence": "进入 AI 深度处理以核对冲突证据。",
            "order_unavailable": "检查订单号或当前登录账号。",
            "tool_failed": "稍后重试，或进入 AI 深度处理。",
            "model_unavailable": "稍后重试。",
        }[stop_reason]
        resolution = ServiceResolution(
            goal=state.get("service_goal_summary") or "联合核对售后问题",
            verified_facts=tuple(verified),
            missing_information=tuple(dict.fromkeys(missing)),
            policy_evidence=tuple(state.get("policy_citations", ())),
            recommendations=tuple(recommendations),
            allowed_actions=tuple(allowed_actions),
            progress=tuple(progress),
            stop_reason=stop_reason,
            next_step=next_step,
        )
        return {
            "service_resolution": resolution,
            "status": (
                "service_guidance_completed"
                if stop_reason == "completed"
                else "service_guidance_needs_input"
                if stop_reason == "needs_user_input"
                else "service_guidance_incomplete"
            ),
            "audit": [f"service_guidance_assembled:{stop_reason}"],
        }

    def respond_service_guidance(self, state: AgentState) -> dict[str, object]:
        """把结构化方案格式化为简洁回复，公开卡片保留完整详情。"""

        resolution = state.get("service_resolution")
        if resolution is None:
            raise ValueError("组合咨询回复前缺少 ServiceResolution")
        lines = [resolution.goal]
        lines.extend(f"- {item.statement}" for item in resolution.verified_facts)
        if resolution.missing_information:
            lines.append("仍需确认：" + "、".join(resolution.missing_information))
        lines.append("下一步：" + resolution.next_step)
        return {
            "messages": [{"role": "assistant", "content": "\n".join(lines)}],
            "audit": ["responded:service_guidance"],
        }


def register_service_guidance_workflow(
    builder: StateGraph,
    dependencies: Dependencies,
) -> None:
    """把组合咨询节点按单次读取顺序注册到唯一主图。"""

    nodes = ServiceGuidanceNodes(dependencies)
    builder.add_node(
        "prepare_service_guidance",
        nodes.prepare_service_guidance,
    )
    builder.add_node("load_guidance_order", nodes.load_guidance_order)
    builder.add_node("load_guidance_shipment", nodes.load_guidance_shipment)
    builder.add_node("retrieve_guidance_policy", nodes.retrieve_guidance_policy)
    builder.add_node("assess_guidance_evidence", nodes.assess_guidance_evidence)
    builder.add_node(
        "assemble_service_resolution",
        nodes.assemble_service_resolution,
    )
    builder.add_node("respond_service_guidance", nodes.respond_service_guidance)
    builder.add_edge("prepare_service_guidance", "load_guidance_order")
    builder.add_edge("load_guidance_order", "load_guidance_shipment")
    builder.add_edge("load_guidance_shipment", "retrieve_guidance_policy")
    builder.add_edge("retrieve_guidance_policy", "assess_guidance_evidence")
    builder.add_edge("assess_guidance_evidence", "assemble_service_resolution")
    builder.add_edge("assemble_service_resolution", "respond_service_guidance")
    builder.add_edge("respond_service_guidance", END)
