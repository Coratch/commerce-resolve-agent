"""为主图注册售后政策检索、校验、澄清和安全回复节点。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from commerce_resolve.gateways import (
    Dependencies,
    PolicyRepository,
    PolicyRepositoryUnavailableError,
)
from commerce_resolve.policy_rules import (
    build_answer_items,
    detect_policy_conflicts,
    find_missing_dimensions,
    format_missing_dimensions,
    format_policy_answer,
    format_policy_conflicts,
    resolve_evidence_facts,
    select_applicable_facts,
    unsupported_aspects,
)
from commerce_resolve.state import AgentState, RunContext

POLICY_INSUFFICIENT_MESSAGE = "当前受控政策没有足够证据支持这个问题，我无法确认。"
POLICY_REPOSITORY_FAILED_MESSAGE = (
    "政策索引不可用或已过期，请先运行 python -m commerce_resolve policy-index build。"
)


def _latest_user_text(state: AgentState) -> str:
    """读取进入政策路径的最新用户文本。"""

    content = state["messages"][-1].content
    if not isinstance(content, str):
        raise ValueError("政策查询只支持文本输入")
    return content


def _required_repository(dependencies: Dependencies) -> PolicyRepository:
    """返回已注入的只读政策仓库，缺失时映射为可公开处理的错误。"""

    if dependencies.policy_repository is None:
        raise PolicyRepositoryUnavailableError("未装配政策仓库")
    return dependencies.policy_repository


class PolicyNodes:
    """封装依赖外部政策仓库的 LangGraph 节点实现。"""

    def __init__(self, dependencies: Dependencies) -> None:
        """保存只读依赖，不创建或持久化数据库连接。"""

        self._dependencies = dependencies

    def prepare_policy_query(self, state: AgentState) -> dict[str, object]:
        """验证当前政策查询，并清理上一轮待补状态。"""

        if state.get("policy_query") is None:
            raise ValueError("政策路径缺少结构化 PolicyQuery")
        return {
            "pending_policy_query": None,
            "error_code": None,
            "audit": ["policy_query_prepared"],
        }

    def retrieve_policy(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """按当前日期从只读索引检索候选证据，不生成政策结论。"""

        query = state.get("policy_query")
        if query is None:
            raise ValueError("检索政策前缺少 PolicyQuery")
        try:
            result = _required_repository(self._dependencies).search(
                _latest_user_text(state),
                query,
                runtime.context.as_of or date.today(),
            )
        except PolicyRepositoryUnavailableError:
            return {
                "policy_evidence_refs": (),
                "policy_index_version": None,
                "status": "temporarily_failed",
                "error_code": "policy_repository_unavailable",
                "audit": ["policy_repository_unavailable"],
            }
        return {
            "policy_evidence_refs": result.evidence_refs,
            "policy_index_version": (f"{result.corpus_version}:{result.corpus_hash}"),
            "error_code": None,
            "audit": ["policy_retrieved"],
        }

    def assess_policy_evidence(self, state: AgentState) -> dict[str, object]:
        """解析候选事实，计算缺失条件并检测有效范围内的冲突。"""

        query = state.get("policy_query")
        if query is None:
            raise ValueError("评估政策证据前缺少 PolicyQuery")
        try:
            facts = resolve_evidence_facts(
                _required_repository(self._dependencies),
                state.get("policy_evidence_refs", ()),
            )
        except PolicyRepositoryUnavailableError:
            return {
                "status": "temporarily_failed",
                "error_code": "policy_citation_unavailable",
                "audit": ["policy_citation_unavailable"],
            }
        missing = find_missing_dimensions(query, facts)
        applicable = select_applicable_facts(query, facts)
        conflicts = detect_policy_conflicts(applicable) if not missing else ()
        if not applicable and not missing:
            return {
                "policy_conflicts": (),
                "missing_policy_dimensions": (),
                "error_code": "policy_insufficient_evidence",
                "audit": ["policy_insufficient_evidence"],
            }
        return {
            "policy_conflicts": conflicts,
            "missing_policy_dimensions": missing,
            "error_code": None,
            "audit": ["policy_evidence_assessed"],
        }

    def select_policy_facts(self, state: AgentState) -> dict[str, object]:
        """选择适用事实，并拒绝任一请求方面缺少来源的部分回答。"""

        query = state.get("policy_query")
        if query is None:
            raise ValueError("选择政策事实前缺少 PolicyQuery")
        try:
            facts = resolve_evidence_facts(
                _required_repository(self._dependencies),
                state.get("policy_evidence_refs", ()),
            )
        except PolicyRepositoryUnavailableError:
            return {
                "selected_policy_fact_ids": (),
                "status": "temporarily_failed",
                "error_code": "policy_citation_unavailable",
                "audit": ["policy_citation_unavailable"],
            }
        selected = select_applicable_facts(query, facts)
        if not selected or unsupported_aspects(query, selected):
            return {
                "selected_policy_fact_ids": (),
                "error_code": "policy_insufficient_evidence",
                "audit": ["policy_insufficient_evidence"],
            }
        return {
            "selected_policy_fact_ids": tuple(fact.fact_id for fact in selected),
            "error_code": None,
            "audit": [
                "policy_facts_selected:" + ",".join(fact.fact_id for fact in selected)
            ],
        }

    def validate_policy_citations(self, state: AgentState) -> dict[str, object]:
        """重新解析所选事实，确保每项结论仍绑定到当前内容哈希。"""

        selected_ids = state.get("selected_policy_fact_ids", ())
        try:
            facts = resolve_evidence_facts(
                _required_repository(self._dependencies),
                state.get("policy_evidence_refs", ()),
            )
        except PolicyRepositoryUnavailableError:
            return {
                "policy_citations": (),
                "status": "temporarily_failed",
                "error_code": "policy_citation_unavailable",
                "audit": ["policy_citation_unavailable"],
            }
        facts_by_id = {fact.fact_id: fact for fact in facts}
        if not selected_ids or any(
            fact_id not in facts_by_id for fact_id in selected_ids
        ):
            return {
                "policy_citations": (),
                "status": "temporarily_failed",
                "error_code": "policy_citation_invalid",
                "audit": ["policy_citation_invalid"],
            }
        citations = tuple(facts_by_id[fact_id].citation for fact_id in selected_ids)
        return {
            "policy_citations": citations,
            "error_code": None,
            "audit": ["policy_citations_validated"],
        }

    def request_policy_context(self, state: AgentState) -> dict[str, object]:
        """保存最小待补查询，并请求用户补充决定政策分支的条件。"""

        query = state.get("policy_query")
        dimensions = state.get("missing_policy_dimensions", ())
        if query is None or not dimensions:
            raise ValueError("请求政策条件前缺少待补查询或维度")
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": format_missing_dimensions(dimensions),
                }
            ],
            "pending_policy_query": query,
            "status": "awaiting_policy_context",
            "audit": ["awaiting_policy_context"],
        }

    def respond_policy_insufficient(self, state: AgentState) -> dict[str, object]:
        """在没有完整证据时明确拒答，不使用模型常识补充。"""

        return {
            "messages": [{"role": "assistant", "content": POLICY_INSUFFICIENT_MESSAGE}],
            "status": "policy_insufficient_evidence",
            "error_code": "policy_insufficient_evidence",
            "audit": ["responded:policy_insufficient_evidence"],
        }

    def respond_policy_conflict(self, state: AgentState) -> dict[str, object]:
        """展示冲突结论及双方引用，不静默选择任一来源。"""

        conflicts = state.get("policy_conflicts", ())
        if not conflicts:
            raise ValueError("生成政策冲突回复前缺少冲突事实")
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": format_policy_conflicts(conflicts),
                }
            ],
            "status": "policy_conflict",
            "error_code": "policy_conflict",
            "audit": ["responded:policy_conflict"],
        }

    def respond_policy_failure(self, state: AgentState) -> dict[str, object]:
        """对索引或引用失败返回脱敏、可操作的统一提示。"""

        return {
            "messages": [
                {"role": "assistant", "content": POLICY_REPOSITORY_FAILED_MESSAGE}
            ],
            "status": "temporarily_failed",
            "audit": ["responded:policy_temporarily_failed"],
        }

    def respond_policy_answer(self, state: AgentState) -> dict[str, object]:
        """仅从已验证事实生成逐项带引用的最终政策回答。"""

        query = state.get("policy_query")
        selected_ids = state.get("selected_policy_fact_ids", ())
        if query is None or not selected_ids:
            raise ValueError("生成政策回答前缺少查询或所选事实")
        try:
            facts = resolve_evidence_facts(
                _required_repository(self._dependencies),
                state.get("policy_evidence_refs", ()),
            )
        except PolicyRepositoryUnavailableError:
            return self.respond_policy_failure(state)
        facts_by_id = {fact.fact_id: fact for fact in facts}
        if any(fact_id not in facts_by_id for fact_id in selected_ids):
            return self.respond_policy_failure(state)
        answer_items = build_answer_items(
            tuple(facts_by_id[fact_id] for fact_id in selected_ids)
        )
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": format_policy_answer(
                        answer_items,
                        specific_order_eligibility=(query.specific_order_eligibility),
                    ),
                }
            ],
            "policy_citations": tuple(item.citation for item in answer_items),
            "pending_policy_query": None,
            "status": "policy_answered",
            "error_code": None,
            "audit": ["completed:policy_answered"],
        }


def route_after_policy_retrieval(
    state: AgentState,
) -> Literal[
    "respond_policy_failure",
    "respond_policy_insufficient",
    "assess_policy_evidence",
]:
    """根据仓库状态和候选证据数量选择后续政策步骤。"""

    if state.get("error_code") == "policy_repository_unavailable":
        return "respond_policy_failure"
    if not state.get("policy_evidence_refs"):
        return "respond_policy_insufficient"
    return "assess_policy_evidence"


def route_after_policy_assessment(
    state: AgentState,
) -> Literal[
    "respond_policy_failure",
    "respond_policy_insufficient",
    "request_policy_context",
    "respond_policy_conflict",
    "select_policy_facts",
]:
    """按失败、证据、缺失条件和冲突优先级路由。"""

    if state.get("error_code") == "policy_citation_unavailable":
        return "respond_policy_failure"
    if state.get("error_code") == "policy_insufficient_evidence":
        return "respond_policy_insufficient"
    if state.get("missing_policy_dimensions"):
        return "request_policy_context"
    if state.get("policy_conflicts"):
        return "respond_policy_conflict"
    return "select_policy_facts"


def route_after_policy_selection(
    state: AgentState,
) -> Literal[
    "respond_policy_failure",
    "respond_policy_insufficient",
    "validate_policy_citations",
]:
    """拒绝不完整选择，并把可验证事实送入引用校验。"""

    if state.get("error_code") == "policy_citation_unavailable":
        return "respond_policy_failure"
    if state.get("error_code") == "policy_insufficient_evidence":
        return "respond_policy_insufficient"
    return "validate_policy_citations"


def route_after_policy_validation(
    state: AgentState,
) -> Literal["respond_policy_failure", "respond_policy_answer"]:
    """只有全部引用通过服务端解析时才允许生成政策回答。"""

    return (
        "respond_policy_failure" if state.get("error_code") else "respond_policy_answer"
    )


def register_policy_workflow(
    builder: StateGraph,
    dependencies: Dependencies,
) -> None:
    """把政策节点、条件边和结束边注册到现有主图。"""

    nodes = PolicyNodes(dependencies)
    builder.add_node("prepare_policy_query", nodes.prepare_policy_query)
    builder.add_node("retrieve_policy", nodes.retrieve_policy)
    builder.add_node("assess_policy_evidence", nodes.assess_policy_evidence)
    builder.add_node("select_policy_facts", nodes.select_policy_facts)
    builder.add_node("validate_policy_citations", nodes.validate_policy_citations)
    builder.add_node("request_policy_context", nodes.request_policy_context)
    builder.add_node(
        "respond_policy_insufficient",
        nodes.respond_policy_insufficient,
    )
    builder.add_node("respond_policy_conflict", nodes.respond_policy_conflict)
    builder.add_node("respond_policy_failure", nodes.respond_policy_failure)
    builder.add_node("respond_policy_answer", nodes.respond_policy_answer)
    builder.add_edge("prepare_policy_query", "retrieve_policy")
    builder.add_conditional_edges("retrieve_policy", route_after_policy_retrieval)
    builder.add_conditional_edges(
        "assess_policy_evidence",
        route_after_policy_assessment,
    )
    builder.add_conditional_edges(
        "select_policy_facts",
        route_after_policy_selection,
    )
    builder.add_conditional_edges(
        "validate_policy_citations",
        route_after_policy_validation,
    )
    for terminal_node in (
        "request_policy_context",
        "respond_policy_insufficient",
        "respond_policy_conflict",
        "respond_policy_failure",
        "respond_policy_answer",
    ):
        builder.add_edge(terminal_node, END)
