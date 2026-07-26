"""验证 v1.1 商品行和售后中心订单读模型。"""

from datetime import UTC, datetime
from pathlib import Path

from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_service_center import (
    SqliteSupportCenterReader,
)
from commerce_resolve.business_models import OrderCreate, OrderItemInput, OrderUpdate


def _registered_repository(
    tmp_path: Path,
) -> tuple[SqliteBusinessRepository, str, str]:
    """创建完成迁移的临时仓库并返回注册用户作用域。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    repository = SqliteBusinessRepository(create_business_engine(database))
    invitation = repository.create_invitation()
    registration = repository.register(
        username="service.owner",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    return repository, registration.user.id, registration.workspace.id


def test_order_items_are_optional_replaceable_and_persistent(tmp_path: Path) -> None:
    """验证旧订单保持空商品行，新商品行可替换并跨 Reader 读取。"""

    repository, user_id, workspace_id = _registered_repository(tmp_path)
    legacy = repository.create_order(
        user_id=user_id,
        workspace_id=workspace_id,
        data=OrderCreate(order_id="ORD-LEGACY", status="processing"),
    )
    repository.create_order(
        user_id=user_id,
        workspace_id=workspace_id,
        data=OrderCreate(
            order_id="ORD-ITEMS",
            status="delivered",
            items=(
                OrderItemInput(
                    sku="SKU-1",
                    title="演示外套",
                    quantity=1,
                    product_category="apparel",
                ),
            ),
        ),
    )
    updated = repository.update_order(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id="ORD-ITEMS",
        data=OrderUpdate(
            items=(
                OrderItemInput(
                    sku="SKU-2",
                    title="演示配件",
                    quantity=2,
                    product_category="general",
                ),
            )
        ),
    )
    detail = SqliteSupportCenterReader(repository.engine).get_order(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id="ORD-ITEMS",
    )

    assert legacy.items == ()
    assert [item.sku for item in updated.items] == ["SKU-2"]
    assert detail is not None
    assert detail.summary.item_count == 1
    assert detail.items[0].title == "演示配件"


def test_support_order_reader_uses_stable_scope_and_cursor(tmp_path: Path) -> None:
    """验证订单列表只返回当前作用域并按稳定游标分页。"""

    repository, user_id, workspace_id = _registered_repository(tmp_path)
    for order_id in ("ORD-A01", "ORD-A02"):
        repository.create_order(
            user_id=user_id,
            workspace_id=workspace_id,
            data=OrderCreate(order_id=order_id, status="processing"),
        )
    reader = SqliteSupportCenterReader(repository.engine)
    first = reader.list_orders(
        user_id=user_id,
        workspace_id=workspace_id,
        limit=1,
    )
    second = reader.list_orders(
        user_id=user_id,
        workspace_id=workspace_id,
        limit=1,
        before=(first[0].updated_at, first[0].order_id),
    )

    assert len(first) == len(second) == 1
    assert first[0].order_id != second[0].order_id
    assert first[0].updated_at <= datetime.now(UTC)
