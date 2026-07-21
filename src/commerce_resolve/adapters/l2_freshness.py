"""使用现有只读 Gateway 重新验证 L2 Observation 的当前来源版本。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import (
    LogisticsGateway,
    OrderGateway,
    PolicyRepository,
    PolicyRepositoryUnavailableError,
    RefundGateway,
)
from commerce_resolve.l2_context import (
    refund_source_fingerprint,
    source_fingerprint,
)
from commerce_resolve.l2_models import (
    L2Observation,
    L2ObservationRefreshResult,
    OrderObservationSource,
    PolicyObservationSource,
    RefundObservationSource,
    ShipmentObservationSource,
)


class GatewayL2FreshnessReader:
    """通过既有授权 Gateway 执行无副作用的 Observation 来源校验。"""

    def __init__(
        self,
        *,
        order_gateway: OrderGateway,
        logistics_gateway: LogisticsGateway,
        policy_repository: PolicyRepository | None,
        refund_gateway: RefundGateway | None,
    ) -> None:
        """保存窄只读依赖；调用身份仍必须由方法参数提供。"""

        self._orders = order_gateway
        self._logistics = logistics_gateway
        self._policies = policy_repository
        self._refunds = refund_gateway

    def refresh(
        self,
        observation: L2Observation,
        *,
        scope: BusinessScope,
        as_of: date,
        step_id: str,
        now: datetime,
    ) -> L2ObservationRefreshResult:
        """重新读取 Observation 当前来源，返回 fresh 替代项或稳定 stale 状态。"""

        metadata = observation.source_metadata
        if metadata is None:
            return L2ObservationRefreshResult(
                freshness="unknown",
                result_code="source_version_unknown",
            )
        if metadata.kind == "order":
            result = self._orders.get_order(scope, metadata.order_id)
            if result.outcome != "found" or result.value is None:
                return self._stale(result.error_code or result.outcome)
            order = result.value
            version = source_fingerprint(order)
            replacement = observation.model_copy(
                update={
                    "observation_id": self._refresh_id(observation, version),
                    "step_id": step_id,
                    "source_ref": order.order_id,
                    "summary": f"订单 {order.order_id} 状态为 {order.status}。",
                    "evidence_ids": (f"order:{order.order_id}:{order.status}",),
                    "observed_at": now,
                    "source_metadata": OrderObservationSource(
                        kind="order",
                        order_id=order.order_id,
                        source_version=version,
                    ),
                }
            )
            return self._fresh(observation, replacement, version)
        if metadata.kind == "shipment":
            result = self._logistics.get_shipment(scope, metadata.order_id)
            if result.outcome != "found" or result.value is None:
                return self._stale(result.error_code or result.outcome)
            shipment = result.value
            version = source_fingerprint(shipment)
            replacement = observation.model_copy(
                update={
                    "observation_id": self._refresh_id(observation, version),
                    "step_id": step_id,
                    "source_ref": shipment.order_id,
                    "summary": (
                        f"订单 {shipment.order_id} 物流状态为 {shipment.status}；"
                        f"最近事件：{shipment.last_event}。"
                    ),
                    "evidence_ids": (
                        f"shipment:{shipment.order_id}:{shipment.status}",
                    ),
                    "observed_at": now,
                    "source_metadata": ShipmentObservationSource(
                        kind="shipment",
                        order_id=shipment.order_id,
                        source_version=version,
                    ),
                }
            )
            return self._fresh(observation, replacement, version)
        if metadata.kind == "refund":
            if self._refunds is None:
                return self._stale("refund_tool_unavailable")
            result = self._refunds.list_refunds(scope, metadata.order_id)
            if result.outcome != "found" or result.value is None:
                return self._stale(result.error_code or result.outcome)
            refunds = result.value
            version = refund_source_fingerprint(refunds)
            summary = (
                "当前没有 Mock 退款记录。"
                if not refunds
                else "；".join(
                    f"退款 {item.refund_id} 状态为 {item.status}"
                    for item in refunds[:5]
                )
            )
            replacement = observation.model_copy(
                update={
                    "observation_id": self._refresh_id(observation, version),
                    "step_id": step_id,
                    "summary": summary,
                    "evidence_ids": tuple(
                        f"refund:{item.refund_id}:{item.status}" for item in refunds[:5]
                    ),
                    "observed_at": now,
                    "source_metadata": RefundObservationSource(
                        kind="refund",
                        order_id=metadata.order_id,
                        source_version=version,
                    ),
                }
            )
            return self._fresh(observation, replacement, version)
        if metadata.kind == "policy":
            if self._policies is None:
                return self._stale("policy_repository_unavailable")
            try:
                facts = tuple(
                    self._policies.resolve_fact(fact.fact_id, fact.content_hash)
                    for fact in metadata.facts
                )
            except PolicyRepositoryUnavailableError:
                return self._stale("policy_repository_unavailable")
            if any(fact is None for fact in facts):
                return self._stale("policy_source_changed")
            replacement = observation.model_copy(
                update={
                    "observation_id": self._refresh_id(
                        observation,
                        metadata.corpus_hash,
                    ),
                    "step_id": step_id,
                    "observed_at": now,
                    "source_metadata": PolicyObservationSource(
                        kind="policy",
                        corpus_version=metadata.corpus_version,
                        corpus_hash=metadata.corpus_hash,
                        facts=metadata.facts,
                    ),
                }
            )
            return L2ObservationRefreshResult(
                freshness="fresh",
                observation=observation,
                changed=False,
                result_code="source_verified",
            )
        return L2ObservationRefreshResult(
            freshness="fresh",
            observation=observation,
            changed=False,
            result_code="source_not_applicable",
        )

    @staticmethod
    def _refresh_id(observation: L2Observation, source_version: str) -> str:
        """根据稳定来源引用和当前版本生成重放稳定的刷新标识。"""

        digest = hashlib.sha256(
            f"{observation.source_type}:{observation.source_ref}:{source_version}".encode()
        ).hexdigest()[:40]
        return f"refresh-{digest}"

    @staticmethod
    def _fresh(
        original: L2Observation,
        replacement: L2Observation,
        current_version: str,
    ) -> L2ObservationRefreshResult:
        """比较来源版本并返回 fresh Observation 和是否变化。"""

        metadata = original.source_metadata
        previous_version = (
            metadata.corpus_hash
            if metadata is not None and metadata.kind == "policy"
            else metadata.source_version
            if metadata is not None
            else None
        )
        changed = previous_version != current_version
        return L2ObservationRefreshResult(
            freshness="fresh",
            observation=replacement if changed else original,
            changed=changed,
            result_code="source_changed" if changed else "source_verified",
        )

    @staticmethod
    def _stale(result_code: str) -> L2ObservationRefreshResult:
        """构造不携带旧正文的 stale 结果，阻止模型使用历史事实。"""

        return L2ObservationRefreshResult(
            freshness="stale",
            result_code=result_code[:80] or "source_unavailable",
        )
