"""实现 L2 Agent 可以选择但不能绕过 Policy 的固定 R0 Tool Registry。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic
from uuid import uuid4

from langgraph.store.base import BaseStore

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import Dependencies
from commerce_resolve.l2_context import (
    refund_source_fingerprint,
    source_fingerprint,
)
from commerce_resolve.l2_memory import list_preferences
from commerce_resolve.l2_models import (
    L2Observation,
    L2ObservationSource,
    L2ToolCall,
    L2ToolName,
    OrderObservationSource,
    PolicyObservationFact,
    PolicyObservationSource,
    PreferenceObservationSource,
    RefundObservationSource,
    ShipmentObservationSource,
)


@dataclass(frozen=True)
class L2ToolContext:
    """携带一次工具调用所需且不可由模型提供的可信上下文。"""

    scope: BusinessScope
    as_of: date
    step_id: str
    dependencies: Dependencies
    store: BaseStore | None


class L2ToolRegistry:
    """把固定工具名映射到职责单一的本地只读 handler。"""

    @property
    def names(self) -> tuple[L2ToolName, ...]:
        """返回编译时固定的完整 R0 工具白名单。"""

        return (
            "get_order",
            "get_shipment",
            "get_refund_status",
            "search_policy",
            "list_confirmed_preferences",
        )

    def execute(
        self,
        call: L2ToolCall,
        context: L2ToolContext,
        *,
        now: datetime,
    ) -> tuple[L2Observation, int]:
        """执行一个已经通过 Policy 的 R0 调用并返回脱敏 Observation 与耗时。"""

        started = monotonic()
        if call.tool == "get_order":
            result = context.dependencies.order_gateway.get_order(
                context.scope,
                call.order_id,
            )
            if result.outcome != "found" or result.value is None:
                observation = self._failure(
                    context.step_id,
                    call.tool,
                    call.order_id,
                    result.error_code or result.outcome,
                    now,
                )
            else:
                order = result.value
                observation = self._success(
                    context.step_id,
                    call.tool,
                    call.order_id,
                    f"订单 {order.order_id} 状态为 {order.status}。",
                    (f"order:{order.order_id}:{order.status}",),
                    now,
                    source_metadata=OrderObservationSource(
                        kind="order",
                        order_id=order.order_id,
                        source_version=source_fingerprint(order),
                    ),
                )
        elif call.tool == "get_shipment":
            result = context.dependencies.logistics_gateway.get_shipment(
                context.scope,
                call.order_id,
            )
            if result.outcome != "found" or result.value is None:
                observation = self._failure(
                    context.step_id,
                    call.tool,
                    call.order_id,
                    result.error_code or result.outcome,
                    now,
                )
            else:
                shipment = result.value
                observation = self._success(
                    context.step_id,
                    call.tool,
                    call.order_id,
                    (
                        f"订单 {shipment.order_id} 物流状态为 {shipment.status}；"
                        f"最近事件：{shipment.last_event}。"
                    ),
                    (f"shipment:{shipment.order_id}:{shipment.status}",),
                    now,
                    source_metadata=ShipmentObservationSource(
                        kind="shipment",
                        order_id=shipment.order_id,
                        source_version=source_fingerprint(shipment),
                    ),
                )
        elif call.tool == "get_refund_status":
            gateway = context.dependencies.refund_gateway
            if gateway is None:
                observation = self._failure(
                    context.step_id,
                    call.tool,
                    call.order_id,
                    "refund_tool_unavailable",
                    now,
                )
            else:
                result = gateway.list_refunds(context.scope, call.order_id)
                if result.outcome != "found" or result.value is None:
                    observation = self._failure(
                        context.step_id,
                        call.tool,
                        call.order_id,
                        result.error_code or result.outcome,
                        now,
                    )
                else:
                    refunds = result.value
                    summary = (
                        "当前没有 Mock 退款记录。"
                        if not refunds
                        else "；".join(
                            f"退款 {item.refund_id} 状态为 {item.status}"
                            for item in refunds[:5]
                        )
                    )
                    observation = self._success(
                        context.step_id,
                        call.tool,
                        call.order_id,
                        summary,
                        tuple(
                            f"refund:{item.refund_id}:{item.status}"
                            for item in refunds[:5]
                        ),
                        now,
                        source_metadata=RefundObservationSource(
                            kind="refund",
                            order_id=call.order_id,
                            source_version=refund_source_fingerprint(refunds),
                        ),
                    )
        elif call.tool == "search_policy":
            repository = context.dependencies.policy_repository
            if repository is None:
                observation = self._failure(
                    context.step_id,
                    call.tool,
                    "policy-index",
                    "policy_repository_unavailable",
                    now,
                )
            else:
                result = repository.search(
                    call.query_text,
                    call.query,
                    context.as_of,
                    limit=6,
                )
                facts = []
                content_hash_by_fact: dict[str, str] = {}
                for evidence in result.evidence_refs:
                    for fact_id in evidence.fact_ids:
                        fact = repository.resolve_fact(fact_id, evidence.content_hash)
                        if fact is not None:
                            facts.append(fact)
                            content_hash_by_fact[fact.fact_id] = evidence.content_hash
                summary = "\n".join(
                    f"[{fact.fact_id}] {fact.claim_text}" for fact in facts[:8]
                )[:3000]
                observation = self._success(
                    context.step_id,
                    call.tool,
                    f"{result.corpus_version}:{result.corpus_hash}",
                    summary or "未检索到可解析的已发布政策事实。",
                    tuple(fact.fact_id for fact in facts[:8]),
                    now,
                    result_code="found" if facts else "insufficient_evidence",
                    source_metadata=PolicyObservationSource(
                        kind="policy",
                        corpus_version=result.corpus_version,
                        corpus_hash=result.corpus_hash,
                        facts=tuple(
                            PolicyObservationFact(
                                fact_id=fact.fact_id,
                                content_hash=content_hash_by_fact[fact.fact_id],
                                rule_key=fact.rule_key,
                                normalized_value=fact.normalized_value,
                            )
                            for fact in facts[:8]
                        ),
                    ),
                )
        else:
            if context.store is None:
                observation = self._failure(
                    context.step_id,
                    call.tool,
                    "preferences",
                    "memory_store_unavailable",
                    now,
                )
            else:
                preferences = list_preferences(
                    context.store,
                    user_id=context.scope.user_id,
                    workspace_id=context.scope.workspace_id,
                )
                summary = (
                    "当前没有已确认长期偏好。"
                    if not preferences
                    else "；".join(
                        f"{item.memory_type}={item.value}" for item in preferences
                    )
                )
                observation = self._success(
                    context.step_id,
                    call.tool,
                    "confirmed-preferences",
                    summary,
                    tuple(f"memory:{item.memory_id}" for item in preferences),
                    now,
                    source_metadata=PreferenceObservationSource(
                        kind="preference",
                        source_version=source_fingerprint(
                            [
                                item.model_dump(mode="json", exclude_none=True)
                                for item in preferences
                            ]
                        ),
                        memory_ids=tuple(item.memory_id for item in preferences),
                    ),
                )
        duration_ms = max(0, int((monotonic() - started) * 1000))
        return observation, duration_ms

    @staticmethod
    def _success(
        step_id: str,
        tool: L2ToolName,
        source_ref: str,
        summary: str,
        evidence_ids: tuple[str, ...],
        now: datetime,
        *,
        result_code: str = "found",
        source_metadata: L2ObservationSource | None = None,
    ) -> L2Observation:
        """构造有界成功 Observation，不暴露内部对象。"""

        return L2Observation(
            observation_id=str(uuid4()),
            step_id=step_id,
            source_type=tool,
            source_ref=source_ref,
            result_code=result_code,
            summary=summary[:3000],
            evidence_ids=evidence_ids,
            observed_at=now,
            source_metadata=source_metadata,
        )

    @staticmethod
    def _failure(
        step_id: str,
        tool: L2ToolName,
        source_ref: str,
        result_code: str,
        now: datetime,
    ) -> L2Observation:
        """构造不含堆栈与数据库细节的工具失败 Observation。"""

        return L2Observation(
            observation_id=str(uuid4()),
            step_id=step_id,
            source_type=tool,
            source_ref=source_ref,
            result_code=result_code,
            summary="受控工具未返回可用结果。",
            evidence_ids=(),
            observed_at=now,
        )
