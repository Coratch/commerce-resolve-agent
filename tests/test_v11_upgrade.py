"""验证 v1.0 业务数据升级到 v1.1 后保持兼容。"""

import sqlite3
from pathlib import Path

from alembic import command

from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    _alembic_config,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_service_center import SqliteSupportCenterReader
from commerce_resolve.business_models import OrderCreate


def test_v10_order_and_conversation_survive_v11_migration(tmp_path: Path) -> None:
    """验证旧订单和会话升级后可读，新增字段使用兼容空值。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    repository = SqliteBusinessRepository(engine)
    invitation = repository.create_invitation()
    registration = repository.register(
        username="upgrade.owner",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    repository.create_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        data=OrderCreate(order_id="ORD-UPGRADE", status="processing"),
    )
    conversation = repository.create_conversation(
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    engine.dispose()

    command.downgrade(_alembic_config(database), "20260721_0005")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4
        assert (
            connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        )

    upgrade_business_database(database)
    upgraded_engine = create_business_engine(database)
    upgraded = SqliteBusinessRepository(upgraded_engine)
    detail = SqliteSupportCenterReader(upgraded_engine).get_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        order_id="ORD-UPGRADE",
    )
    restored_conversation = upgraded.get_authorized_conversation(
        thread_id=conversation.thread_id,
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    upgraded_engine.dispose()

    assert detail is not None
    assert detail.items == ()
    assert restored_conversation is not None
    assert restored_conversation.related_order_id is None
