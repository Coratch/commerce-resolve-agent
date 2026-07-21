"""验证 v0.4 Mock 支付与退款持久化的金额、作用域和约束。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from commerce_resolve.adapters.sqlite_business import (
    BusinessDataError,
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_refunds import SqliteRefundRepository
from commerce_resolve.business_models import (
    MockPaymentInput,
    OrderCreate,
    amount_to_minor_units,
    format_minor_units,
)
from commerce_resolve.models import RefundPreview, RefundReason


def _registered_order(
    repository: SqliteBusinessRepository,
    *,
    username: str,
    order_id: str,
) -> tuple[str, str]:
    """注册私有工作区并创建一条最小处理中订单。"""

    invitation = repository.create_invitation()
    registration = repository.register(
        username=username,
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    repository.create_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        data=OrderCreate(order_id=order_id, status="processing"),
    )
    return registration.user.id, registration.workspace.id


def _preview(*, action_id: str, task_id: str, marker: str) -> RefundPreview:
    """构造同一订单对应的服务端退款预览，用于竞争约束测试。"""

    return RefundPreview(
        action_id=action_id,
        task_id=task_id,
        order_id="ORD-REFUND-RACE",
        reason=RefundReason(code="quality_issue"),
        amount_minor=12990,
        display_amount="129.90",
        currency="CNY",
        channel="mock_card",
        order_status="processing",
        shipment_status=None,
        payment_status="settled",
        policy_fact_ids=("refund.eligibility.pre_fulfillment",),
        citations=(),
        policy_version="test-v1",
        facts_fingerprint="f" * 64,
        preview_hash=marker * 64,
    )


def test_v04_migration_creates_transaction_tables(tmp_path: Path) -> None:
    """验证项目迁移入口在旧业务表上增加四张退款职责表。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)

    tables = set(inspect(engine).get_table_names())

    assert {
        "mock_payments",
        "refund_actions",
        "mock_refunds",
        "refund_audit_events",
    } <= tables
    engine.dispose()


@pytest.mark.parametrize(
    ("amount", "minor"),
    [("0.01", 1), ("129.90", 12990), ("1000000000.00", 100000000000)],
)
def test_money_conversion_is_lossless(amount: str, minor: int) -> None:
    """验证公开定点字符串与内部整数分可以无损往返。"""

    assert amount_to_minor_units(amount) == minor
    assert format_minor_units(minor) == amount


@pytest.mark.parametrize("amount", ["0.00", "1", "1.2", "01.00", "1.001", "-1.00"])
def test_money_conversion_rejects_ambiguous_values(amount: str) -> None:
    """验证零值、负数和非固定两位小数不会进入交易事实。"""

    with pytest.raises(ValueError):
        amount_to_minor_units(amount)


def test_payment_is_scoped_and_blocks_order_deletion(tmp_path: Path) -> None:
    """验证支付按私有工作区隔离，且已有交易事实的订单不能删除。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine)
    refunds = SqliteRefundRepository(engine)
    user_a, workspace_a = _registered_order(
        business,
        username="refund.a",
        order_id="ORD-REFUND-A",
    )
    user_b, workspace_b = _registered_order(
        business,
        username="refund.b",
        order_id="ORD-REFUND-B",
    )

    payment = refunds.upsert_payment(
        user_id=user_a,
        workspace_id=workspace_a,
        order_id="ORD-REFUND-A",
        data=MockPaymentInput(
            amount="129.90",
            channel="mock_card",
            status="settled",
        ),
    )

    assert payment.amount_minor == 12990
    assert payment.status == "settled"
    with pytest.raises(BusinessDataError, match="order_not_accessible"):
        refunds.get_payment(
            user_id=user_b,
            workspace_id=workspace_b,
            order_id="ORD-REFUND-A",
        )
    with pytest.raises(BusinessDataError, match="order_has_transaction_data"):
        business.delete_order(
            user_id=user_a,
            workspace_id=workspace_a,
            order_id="ORD-REFUND-A",
        )
    assert refunds.count_refunds() == 0
    engine.dispose()


def test_concurrent_threads_reserve_only_one_active_refund_action(
    tmp_path: Path,
) -> None:
    """验证两个 thread 同时申请同一订单时只有一个动作成功保留。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine)
    refunds = SqliteRefundRepository(engine)
    user_id, workspace_id = _registered_order(
        business,
        username="refund.race",
        order_id="ORD-REFUND-RACE",
    )
    refunds.upsert_payment(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id="ORD-REFUND-RACE",
        data=MockPaymentInput(
            amount="129.90",
            channel="mock_card",
            status="settled",
        ),
    )
    conversations = tuple(
        business.create_conversation(
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
        )
        for _ in range(2)
    )
    previews = (
        _preview(
            action_id=str(uuid4()),
            task_id=conversations[0].thread_id,
            marker="a",
        ),
        _preview(
            action_id=str(uuid4()),
            task_id=conversations[1].thread_id,
            marker="b",
        ),
    )
    barrier = Barrier(2)

    def reserve(preview: RefundPreview) -> str:
        """同步发起一次预览保留，并把稳定结果返回给测试断言。"""

        barrier.wait()
        try:
            refunds.reserve_preview(
                user_id=user_id,
                workspace_id=workspace_id,
                preview=preview,
            )
        except BusinessDataError as error:
            return error.error_code
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(reserve, previews))

    assert sorted(results) == ["refund_conflict", "reserved"]
    assert refunds.count_refunds() == 0
    assert refunds.count_audit_events() == 1
    engine.dispose()
