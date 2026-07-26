"""验证 v0.3 业务数据库迁移、认证、隔离和配额。"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect

from commerce_resolve.adapters.sqlite_business import (
    BusinessDataError,
    SqliteBusinessRepository,
    assert_business_schema_current,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.auth import AuthDomainError
from commerce_resolve.business_models import (
    OrderCreate,
    OrderUpdate,
    ShipmentInput,
)

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path: Path) -> SqliteBusinessRepository:
    """创建迁移到 head 且使用固定时钟的临时业务仓库。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    assert_business_schema_current(engine, database)
    repo = SqliteBusinessRepository(engine, now_provider=lambda: NOW)
    yield repo
    engine.dispose()


def _register(
    repository: SqliteBusinessRepository,
    username: str,
) -> tuple[str, str]:
    """签发一次性邀请码并返回新用户和工作区 ID。"""

    invite = repository.create_invitation()
    result = repository.register(
        username=username,
        password="correct horse battery",
        invitation_code=invite.code,
    )
    return result.user.id, result.workspace.id


def test_migration_creates_all_separated_business_tables(tmp_path: Path) -> None:
    """验证空数据库通过 Alembic 创建 v0.3 全部业务表。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)

    tables = set(inspect(engine).get_table_names())

    assert {
        "alembic_version",
        "users",
        "workspaces",
        "invitations",
        "web_sessions",
        "conversations",
        "orders",
        "shipments",
        "llm_daily_usage",
    } <= tables
    assert_business_schema_current(engine, database)
    engine.dispose()


def test_invitation_registration_is_single_use_and_atomic(
    repository: SqliteBusinessRepository,
) -> None:
    """验证重复注册不会增加账号或超额消费邀请码。"""

    invite = repository.create_invitation(max_uses=1)
    result = repository.register(
        username="User.One",
        password="correct horse battery",
        invitation_code=invite.code,
    )

    with pytest.raises(AuthDomainError, match="invitation_unavailable"):
        repository.register(
            username="user.two",
            password="correct horse battery",
            invitation_code=invite.code,
        )

    assert result.user.username == "user.one"
    assert repository.count_users() == 1
    assert repository.invitation_usage(invite.id) == 1


@pytest.mark.parametrize("mode", ["expired", "revoked", "unknown"])
def test_unavailable_invitations_share_public_failure(
    repository: SqliteBusinessRepository,
    mode: str,
) -> None:
    """验证过期、撤销和未知邀请码使用相同领域错误。"""

    if mode == "unknown":
        code = "not-an-invitation"
    else:
        invite = repository.create_invitation(expires_in_hours=1)
        code = invite.code
        if mode == "expired":
            repository._now = lambda: NOW + timedelta(hours=2)
        else:
            repository.revoke_invitation(invite.id)

    with pytest.raises(AuthDomainError, match="invitation_unavailable"):
        repository.register(
            username=f"user-{mode}",
            password="correct horse battery",
            invitation_code=code,
        )

    assert repository.count_users() == 0


def test_authentication_uses_uniform_failure_and_revocable_sessions(
    repository: SqliteBusinessRepository,
) -> None:
    """验证登录失败不区分账号存在性，Session 可轮换和撤销。"""

    invite = repository.create_invitation()
    registration = repository.register(
        username="user.one",
        password="correct horse battery",
        invitation_code=invite.code,
    )
    authenticated = repository.authenticate("USER.ONE", "correct horse battery")

    for username, password in (
        ("missing", "correct horse battery"),
        ("user.one", "incorrect password"),
    ):
        with pytest.raises(AuthDomainError, match="authentication_failed"):
            repository.authenticate(username, password)

    bundle = repository.create_registered_session(authenticated)
    identity = repository.resolve_session(bundle.session_token)
    assert identity is not None
    assert identity.user_id == registration.user.id
    assert identity.workspace_id == registration.workspace.id

    csrf = repository.rotate_csrf(bundle.session_token)
    assert csrf is not None
    assert repository.verify_csrf(bundle.session_token, csrf) == identity
    assert repository.verify_csrf(bundle.session_token, "wrong") is None

    assert repository.revoke_session(bundle.session_token) is True
    assert repository.resolve_session(bundle.session_token) is None


def test_guest_sessions_have_unique_subjects_and_shared_demo_workspace(
    repository: SqliteBusinessRepository,
) -> None:
    """验证游客身份彼此隔离但只指向共享只读 demo 工作区。"""

    first = repository.create_guest_session()
    second = repository.create_guest_session()

    assert first.subject_id != second.subject_id
    assert first.workspace_id == second.workspace_id == "demo"
    assert first.user_id is None
    assert second.user_id is None


def test_conversations_require_exact_subject_workspace_and_mode(
    repository: SqliteBusinessRepository,
) -> None:
    """验证 thread 在读取 Checkpoint 前按完整身份作用域授权。"""

    conversation = repository.create_conversation(
        subject_id="subject-a",
        workspace_id="demo",
        access_mode="guest",
    )

    assert (
        repository.get_authorized_conversation(
            thread_id=conversation.thread_id,
            subject_id="subject-a",
            workspace_id="demo",
            access_mode="guest",
        )
        == conversation
    )
    assert (
        repository.get_authorized_conversation(
            thread_id=conversation.thread_id,
            subject_id="subject-b",
            workspace_id="demo",
            access_mode="guest",
        )
        is None
    )


def test_private_orders_support_crud_and_cross_workspace_isolation(
    repository: SqliteBusinessRepository,
) -> None:
    """验证同号订单按工作区隔离且物流关系随订单事务更新。"""

    user_a, workspace_a = _register(repository, "user.a")
    user_b, workspace_b = _register(repository, "user.b")
    data = OrderCreate(
        order_id="ORD-SAME",
        status="processing",
        shipment=ShipmentInput(
            status="preparing",
            last_event="等待揽收",
        ),
    )

    order_a = repository.create_order(
        user_id=user_a, workspace_id=workspace_a, data=data
    )
    order_b = repository.create_order(
        user_id=user_b, workspace_id=workspace_b, data=data
    )

    assert order_a.order_id == order_b.order_id == "ORD-SAME"
    assert order_a in repository.list_orders(user_id=user_a, workspace_id=workspace_a)
    with pytest.raises(BusinessDataError, match="order_not_accessible"):
        repository.get_order_record(
            user_id=user_a,
            workspace_id=workspace_b,
            order_id="ORD-SAME",
        )

    updated = repository.update_order(
        user_id=user_a,
        workspace_id=workspace_a,
        order_id="ORD-SAME",
        data=OrderUpdate(
            status="shipped",
            shipment=ShipmentInput(
                status="in_transit",
                last_event="包裹运输中",
                estimated_delivery_at=date(2026, 7, 20),
            ),
        ),
    )
    assert updated.status == "shipped"
    assert updated.shipment is not None
    assert updated.shipment.status == "in_transit"

    assert repository.delete_order(
        user_id=user_a,
        workspace_id=workspace_a,
        order_id="ORD-SAME",
    )
    remaining = repository.list_orders(user_id=user_a, workspace_id=workspace_a)
    assert len(remaining) == 3
    assert all(order.order_id != "ORD-SAME" for order in remaining)
    other_workspace = repository.list_orders(
        user_id=user_b,
        workspace_id=workspace_b,
    )
    assert len(other_workspace) == 4
    assert order_b in other_workspace


def test_llm_quota_is_atomically_limited(
    repository: SqliteBusinessRepository,
) -> None:
    """验证达到日调用上限后不再增加已接受调用数。"""

    user_id, _ = _register(repository, "quota.user")
    usage_date = date(2026, 7, 17)

    assert repository.accept_llm_call(user_id, usage_date, 2) is True
    assert repository.accept_llm_call(user_id, usage_date, 2) is True
    assert repository.accept_llm_call(user_id, usage_date, 2) is False
    assert repository.get_llm_usage(user_id, usage_date).accepted_calls == 2
