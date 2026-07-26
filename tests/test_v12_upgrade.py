"""验证 v1.1 业务数据升级到 v1.2 后角色和原有事实兼容。"""

import sqlite3
from pathlib import Path

from alembic import command

from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    _alembic_config,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.business_models import OrderCreate


def test_v11_account_session_and_order_survive_v12_migration(tmp_path: Path) -> None:
    """验证既有账号默认成为客户，Session 和订单可读且角色即时生效。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    repository = SqliteBusinessRepository(engine)
    invitation = repository.create_invitation()
    registration = repository.register(
        username="upgrade.v12",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    session = repository.create_registered_session(registration)
    repository.create_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        data=OrderCreate(order_id="ORD-V12-UPGRADE", status="processing"),
    )
    engine.dispose()

    command.downgrade(_alembic_config(database), "20260722_0006")
    with sqlite3.connect(database) as connection:
        user_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(users)")
        }
        assert "role" not in user_columns
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4

    upgrade_business_database(database)
    upgraded_engine = create_business_engine(database)
    upgraded = SqliteBusinessRepository(upgraded_engine)
    identity = upgraded.resolve_session(session.session_token)
    order = upgraded.get_order_record(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        order_id="ORD-V12-UPGRADE",
    )
    admin = SqliteAdminRepository(upgraded_engine)
    account = admin.get_customer(registration.user.id)
    admin.set_role("upgrade.v12", "admin")
    elevated_identity = upgraded.resolve_session(session.session_token)
    with sqlite3.connect(database) as connection:
        audit_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='admin_action_audit'"
        ).fetchone()
    upgraded_engine.dispose()

    assert identity is not None and identity.user_role == "customer"
    assert account is not None and account.role == "customer"
    assert order.order_id == "ORD-V12-UPGRADE"
    assert elevated_identity is not None and elevated_identity.user_role == "admin"
    assert audit_table == ("admin_action_audit",)
