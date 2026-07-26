"""验证 v1.2 业务数据升级到 v1.3 后保持可读且不补造快照。"""

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
from commerce_resolve.business_models import OrderCreate, OrderItemInput


def test_v12_order_survives_v13_migration_as_legacy_snapshot(
    tmp_path: Path,
) -> None:
    """验证历史商品行升级后新字段为空，订单和会话事实仍可读取。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    repository = SqliteBusinessRepository(engine)
    invitation = repository.create_invitation()
    registration = repository.register(
        username="upgrade.v13",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    repository.create_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        data=OrderCreate(
            order_id="ORD-V13-UPGRADE",
            status="shipped",
            items=(
                OrderItemInput(
                    sku="LEGACY-SKU",
                    title="升级前商品",
                    quantity=1,
                ),
            ),
        ),
    )
    engine.dispose()

    command.downgrade(_alembic_config(database), "20260722_0007")
    with sqlite3.connect(database) as connection:
        order_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(orders)")
        }
        item_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(order_items)")
        }
        assert "catalog_version" not in order_columns
        assert "image_ref" not in item_columns
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4

    upgrade_business_database(database)
    upgraded_engine = create_business_engine(database)
    detail = SqliteSupportCenterReader(upgraded_engine).get_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        order_id="ORD-V13-UPGRADE",
    )
    with sqlite3.connect(database) as connection:
        values = connection.execute(
            "SELECT product_ref, variant_title, unit_amount_minor, image_ref, "
            "catalog_version FROM order_items"
        ).fetchone()
        package_count = connection.execute(
            "SELECT COUNT(*) FROM shipment_packages"
        ).fetchone()[0]
    upgraded_engine.dispose()

    assert detail is not None
    assert detail.items[0].title == "升级前商品"
    assert detail.items[0].image_url is None
    assert detail.items[0].unit_amount is None
    assert values == (None, None, None, None, None)
    assert package_count == 0
