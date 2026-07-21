"""验证固定 L2 R0 工具的作用域、结果和长期偏好读取契约。"""

from datetime import UTC, date, datetime

from langgraph.store.memory import InMemoryStore

from commerce_resolve.access import BusinessScope
from commerce_resolve.adapters.fake import FakeLogisticsGateway, FakeOrderGateway
from commerce_resolve.adapters.fake_refunds import FakeRefundGateway
from commerce_resolve.gateways import Dependencies
from commerce_resolve.l2_memory import confirm_preference
from commerce_resolve.l2_models import (
    GetOrderCall,
    GetRefundStatusCall,
    GetShipmentCall,
    ListConfirmedPreferencesCall,
    MemoryProposal,
)
from commerce_resolve.l2_tools import L2ToolContext, L2ToolRegistry
from commerce_resolve.models import OrderView, RefundContext, ShipmentView

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
SCOPE = BusinessScope(user_id="u1", workspace_id="w1", access_mode="registered")


def _dependencies() -> Dependencies:
    """构造只包含当前账号订单、物流和退款事实的 Fake 依赖。"""

    order = OrderView(order_id="ORD-001", user_id="u1", status="shipped")
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="已离开分拨中心",
        estimated_delivery_at=date(2026, 7, 22),
    )
    refund = FakeRefundGateway(
        {
            ("u1", "w1", "ORD-001"): RefundContext(
                order_id="ORD-001",
                order_status="shipped",
                shipment_status="in_transit",
            )
        }
    )
    return Dependencies(
        interpreter=None,  # type: ignore[arg-type]
        order_gateway=FakeOrderGateway({("u1", "ORD-001"): order}),
        logistics_gateway=FakeLogisticsGateway({"ORD-001": shipment}),
        refund_gateway=refund,
    )


def _context(store: InMemoryStore | None = None) -> L2ToolContext:
    """构造模型无法改写的可信工具执行上下文。"""

    return L2ToolContext(
        scope=SCOPE,
        as_of=date(2026, 7, 20),
        step_id="step-001",
        dependencies=_dependencies(),
        store=store,
    )


def test_order_and_shipment_tools_return_bounded_evidence() -> None:
    """验证订单和物流工具返回可引用摘要而不是内部业务对象。"""

    registry = L2ToolRegistry()
    order, _ = registry.execute(
        GetOrderCall(tool="get_order", order_id="ORD-001"),
        _context(),
        now=NOW,
    )
    shipment, _ = registry.execute(
        GetShipmentCall(tool="get_shipment", order_id="ORD-001"),
        _context(),
        now=NOW,
    )

    assert order.result_code == "found"
    assert order.evidence_ids == ("order:ORD-001:shipped",)
    assert shipment.result_code == "found"
    assert shipment.evidence_ids == ("shipment:ORD-001:in_transit",)


def test_tool_scope_blocks_other_users_order_and_refund_visibility() -> None:
    """验证模型提供订单号也不能绕过服务端用户与工作区作用域。"""

    other = L2ToolContext(
        scope=BusinessScope(user_id="u2", workspace_id="w2", access_mode="registered"),
        as_of=date(2026, 7, 20),
        step_id="step-002",
        dependencies=_dependencies(),
        store=None,
    )
    registry = L2ToolRegistry()

    order, _ = registry.execute(
        GetOrderCall(tool="get_order", order_id="ORD-001"), other, now=NOW
    )
    refund, _ = registry.execute(
        GetRefundStatusCall(tool="get_refund_status", order_id="ORD-001"),
        other,
        now=NOW,
    )

    assert order.result_code == "order_unavailable"
    assert refund.result_code == "order_unavailable"
    assert order.evidence_ids == refund.evidence_ids == ()


def test_confirmed_preference_tool_reads_only_current_namespace() -> None:
    """验证偏好工具只读取当前用户已确认且受限的长期记忆。"""

    store = InMemoryStore()
    confirm_preference(
        store,
        user_id="u1",
        workspace_id="w1",
        proposal=MemoryProposal(
            proposal_id="proposal-001",
            case_id="case-001",
            memory_type="response_detail",
            value="concise",
            purpose="后续客服采用该回复详细程度",
        ),
        now=NOW,
    )

    observation, _ = L2ToolRegistry().execute(
        ListConfirmedPreferencesCall(tool="list_confirmed_preferences"),
        _context(store),
        now=NOW,
    )

    assert observation.result_code == "found"
    assert "response_detail=concise" in observation.summary
    assert len(observation.evidence_ids) == 1
