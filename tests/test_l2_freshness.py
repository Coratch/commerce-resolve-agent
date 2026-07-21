"""验证 v0.7 L2 Observation 的调用前来源刷新语义。"""

from datetime import UTC, date, datetime

from commerce_resolve.access import BusinessScope
from commerce_resolve.adapters.fake import FakeLogisticsGateway, FakeOrderGateway
from commerce_resolve.adapters.l2_freshness import GatewayL2FreshnessReader
from commerce_resolve.business_models import MockRefundRecord
from commerce_resolve.l2_context import refund_source_fingerprint, source_fingerprint
from commerce_resolve.l2_models import (
    L2Observation,
    OrderObservationSource,
    PolicyObservationFact,
    PolicyObservationSource,
    RefundObservationSource,
    ShipmentObservationSource,
)
from commerce_resolve.models import OrderView, ShipmentView, ToolResult

NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
SCOPE = BusinessScope(user_id="u1", workspace_id="w1", access_mode="registered")


class _RefundReader:
    """提供只读退款集合，隔离 Freshness Reader 与退款执行能力。"""

    def __init__(self, refunds: tuple[MockRefundRecord, ...]) -> None:
        """保存测试所需的当前退款集合。"""

        self._refunds = refunds

    def list_refunds(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[tuple[MockRefundRecord, ...]]:
        """仅对当前测试作用域和订单返回退款集合。"""

        if scope != SCOPE or order_id != "ORD-001":
            return ToolResult(outcome="unavailable", error_code="order_unavailable")
        return ToolResult(outcome="found", value=self._refunds)


class _PolicyReader:
    """按事实标识模拟当前政策来源是否仍可解析。"""

    def __init__(self, *, available: bool) -> None:
        """配置政策事实是否仍存在。"""

        self._available = available

    def resolve_fact(self, fact_id: str, expected_hash: str) -> object | None:
        """在标识与哈希非空且来源可用时返回占位事实。"""

        if self._available and fact_id and expected_hash:
            return object()
        return None


def _observation(*, kind: str, source_metadata: object) -> L2Observation:
    """构造一条带旧来源版本的统一测试 Observation。"""

    return L2Observation(
        observation_id=f"observation-{kind}",
        step_id="step-old",
        source_type=kind,
        source_ref="ORD-001" if kind != "policy" else "refund-window",
        result_code="found",
        summary="旧事实摘要",
        evidence_ids=(f"{kind}:old",),
        observed_at=NOW,
        source_metadata=source_metadata,  # type: ignore[arg-type]
    )


def _reader(
    *,
    order: OrderView,
    shipment: ShipmentView,
    refunds: tuple[MockRefundRecord, ...] = (),
    policy_available: bool = True,
) -> GatewayL2FreshnessReader:
    """装配四类只读 Fake 来源。"""

    return GatewayL2FreshnessReader(
        order_gateway=FakeOrderGateway({("u1", "ORD-001"): order}),
        logistics_gateway=FakeLogisticsGateway({"ORD-001": shipment}),
        refund_gateway=_RefundReader(refunds),  # type: ignore[arg-type]
        policy_repository=_PolicyReader(available=policy_available),  # type: ignore[arg-type]
    )


def test_order_and_shipment_changes_replace_old_observations() -> None:
    """验证订单和物流变化后返回当前版本，而不是继续使用旧摘要。"""

    old_order = OrderView(order_id="ORD-001", user_id="u1", status="shipped")
    current_order = old_order.model_copy(update={"status": "delivered"})
    old_shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="运输中",
    )
    current_shipment = old_shipment.model_copy(
        update={"status": "delivered", "last_event": "已签收"}
    )
    reader = _reader(order=current_order, shipment=current_shipment)

    order_result = reader.refresh(
        _observation(
            kind="order",
            source_metadata=OrderObservationSource(
                kind="order",
                order_id="ORD-001",
                source_version=source_fingerprint(old_order),
            ),
        ),
        scope=SCOPE,
        as_of=date(2026, 7, 21),
        step_id="step-new",
        now=NOW,
    )
    shipment_result = reader.refresh(
        _observation(
            kind="shipment",
            source_metadata=ShipmentObservationSource(
                kind="shipment",
                order_id="ORD-001",
                source_version=source_fingerprint(old_shipment),
            ),
        ),
        scope=SCOPE,
        as_of=date(2026, 7, 21),
        step_id="step-new",
        now=NOW,
    )

    assert order_result.changed is shipment_result.changed is True
    assert order_result.observation is not None
    assert shipment_result.observation is not None
    assert "delivered" in order_result.observation.summary
    assert "已签收" in shipment_result.observation.summary
    assert order_result.observation.step_id == "step-new"
    assert shipment_result.observation.step_id == "step-new"


def test_refund_change_replaces_old_refund_set() -> None:
    """验证退款集合版本变化后刷新摘要和证据。"""

    refund = MockRefundRecord(
        refund_id="refund-001",
        action_id="action-001",
        order_id="ORD-001",
        amount_minor=8800,
        currency="CNY",
        channel="mock_card",
        status="succeeded",
        gateway_result_code="mock_succeeded",
        created_at=NOW,
        updated_at=NOW,
    )
    order = OrderView(order_id="ORD-001", user_id="u1", status="delivered")
    shipment = ShipmentView(
        order_id="ORD-001",
        status="delivered",
        last_event="已签收",
    )
    reader = _reader(order=order, shipment=shipment, refunds=(refund,))
    old_version = refund_source_fingerprint(())

    result = reader.refresh(
        _observation(
            kind="refund",
            source_metadata=RefundObservationSource(
                kind="refund",
                order_id="ORD-001",
                source_version=old_version,
            ),
        ),
        scope=SCOPE,
        as_of=date(2026, 7, 21),
        step_id="step-new",
        now=NOW,
    )

    assert result.changed is True
    assert result.observation is not None
    assert result.observation.evidence_ids == ("refund:refund-001:succeeded",)
    assert "succeeded" in result.observation.summary


def test_unknown_and_unavailable_sources_never_pose_as_fresh() -> None:
    """验证旧 Schema 和不可读取来源分别返回 unknown 与 stale。"""

    order = OrderView(order_id="ORD-001", user_id="u1", status="shipped")
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="运输中",
    )
    reader = _reader(order=order, shipment=shipment)
    unknown = L2Observation(
        observation_id="legacy-observation",
        step_id="legacy-step",
        source_type="get_order",
        source_ref="ORD-001",
        result_code="found",
        summary="旧版本没有来源信息",
        observed_at=NOW,
    )
    unavailable_reader = GatewayL2FreshnessReader(
        order_gateway=FakeOrderGateway({}),
        logistics_gateway=FakeLogisticsGateway({}),
        refund_gateway=None,
        policy_repository=None,
    )

    unknown_result = reader.refresh(
        unknown,
        scope=SCOPE,
        as_of=date(2026, 7, 21),
        step_id="step-new",
        now=NOW,
    )
    stale_result = unavailable_reader.refresh(
        _observation(
            kind="order",
            source_metadata=OrderObservationSource(
                kind="order",
                order_id="ORD-001",
                source_version=source_fingerprint(order),
            ),
        ),
        scope=SCOPE,
        as_of=date(2026, 7, 21),
        step_id="step-new",
        now=NOW,
    )

    assert unknown_result.freshness == "unknown"
    assert unknown_result.observation is None
    assert stale_result.freshness == "stale"
    assert stale_result.observation is None


def test_policy_source_requires_every_fact_to_still_resolve() -> None:
    """验证政策事实缺失时停止信任旧政策 Observation。"""

    fact = PolicyObservationFact(
        fact_id="refund-window",
        content_hash="a" * 64,
        rule_key="refund.window_days",
        normalized_value="7",
    )
    metadata = PolicyObservationSource(
        kind="policy",
        corpus_version="2026-07",
        corpus_hash="b" * 64,
        facts=(fact,),
    )
    order = OrderView(order_id="ORD-001", user_id="u1", status="delivered")
    shipment = ShipmentView(
        order_id="ORD-001",
        status="delivered",
        last_event="已签收",
    )
    reader = _reader(
        order=order,
        shipment=shipment,
        policy_available=False,
    )

    result = reader.refresh(
        _observation(kind="policy", source_metadata=metadata),
        scope=SCOPE,
        as_of=date(2026, 7, 21),
        step_id="step-new",
        now=NOW,
    )

    assert result.freshness == "stale"
    assert result.result_code == "policy_source_changed"
    assert result.observation is None
