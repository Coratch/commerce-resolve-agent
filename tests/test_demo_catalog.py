"""验证 v1.3 演示目录、资源校验、场景初始化与订单快照。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_service_center import SqliteSupportCenterReader
from commerce_resolve.demo_catalog import DemoCatalogError, DemoCatalogService

PASSWORD = "correct horse battery"


def _registered_customer(
    database: Path,
    *,
    username: str = "catalog.owner",
):
    """创建迁移完成的临时业务库和一个可播种的注册客户。"""

    upgrade_business_database(database)
    engine = create_business_engine(database)
    repository = SqliteBusinessRepository(engine)
    invitation = repository.create_invitation()
    registration = repository.register(
        username=username,
        password=PASSWORD,
        invitation_code=invitation.code,
    )
    return engine, repository, registration


def test_v13_catalog_validates_counts_and_local_assets() -> None:
    """验证目录规模、SKU 数量和全部本地资源摘要。"""

    summary = DemoCatalogService().summary()

    assert summary.catalog_version == "v1.3"
    assert summary.product_count == 12
    assert summary.sku_count == 19
    assert summary.persona_count == 3
    assert summary.scenario_count == 10
    assert all(product.image_ref for product in summary.products)


def test_catalog_rejects_a_modified_asset(tmp_path: Path) -> None:
    """验证本地商品图被修改后，目录会在任何写入前拒绝。"""

    shutil.copytree("data/demo", tmp_path / "data/demo")
    shutil.copytree(
        "frontend/public/catalog",
        tmp_path / "frontend/public/catalog",
    )
    asset = tmp_path / "frontend/public/catalog/v1.3/backpack.webp"
    asset.write_bytes(asset.read_bytes() + b"tampered")

    with pytest.raises(DemoCatalogError) as captured:
        DemoCatalogService(project_root=tmp_path).load()

    assert captured.value.error_code == "catalog_asset_invalid"


def test_seed_is_idempotent_and_persists_snapshot_packages_and_payment(
    tmp_path: Path,
) -> None:
    """验证同一场景只创建一次，并写入商品快照、包裹和支付事实。"""

    engine, _repository, registration = _registered_customer(
        tmp_path / "business.sqlite"
    )
    service = DemoCatalogService(engine=engine)

    first = service.seed(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        scenario_id="partial-fulfillment",
    )
    second = service.seed(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        scenario_id="partial-fulfillment",
    )
    detail = SqliteSupportCenterReader(engine).get_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        order_id=first.order_id,
    )
    engine.dispose()

    assert first.created is True
    assert second.created is False
    assert second.order_id == first.order_id
    assert detail is not None
    assert len(detail.items) >= 2
    assert len(detail.packages) == 2
    assert all(item.product_ref and item.image_url for item in detail.items)
    assert all(item.unit_amount is not None for item in detail.items)
    assert detail.amount_summary is not None
    assert detail.amount_summary.item_subtotal == detail.amount_summary.paid_amount
    assert detail.summary.fulfillment_summary == "1/2 个包裹已送达"


def test_admin_request_id_is_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    """验证管理员请求 ID 可重放，但不能绑定到另一个场景。"""

    engine, _repository, registration = _registered_customer(
        tmp_path / "business.sqlite"
    )
    admin = SqliteAdminRepository(engine)
    admin.set_role("catalog.owner", "admin")
    service = DemoCatalogService(engine=engine)

    first = service.seed(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        scenario_id="single-package-shipping",
        admin_user_id=registration.user.id,
        client_request_id="seed-request-001",
    )
    repeated = service.seed(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        scenario_id="single-package-shipping",
        admin_user_id=registration.user.id,
        client_request_id="seed-request-001",
    )
    with pytest.raises(DemoCatalogError) as captured:
        service.seed(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            scenario_id="delivered-refundable",
            admin_user_id=registration.user.id,
            client_request_id="seed-request-001",
        )
    engine.dispose()

    assert first.created is True
    assert repeated.created is False
    assert repeated.order_id == first.order_id
    assert captured.value.error_code == "catalog_seed_request_conflict"


def test_catalog_template_change_does_not_rewrite_order_snapshot(
    tmp_path: Path,
) -> None:
    """验证目录模板修改后，既有订单仍从数据库快照读取原商品信息。"""

    shutil.copytree("data/demo", tmp_path / "data/demo")
    shutil.copytree(
        "frontend/public/catalog",
        tmp_path / "frontend/public/catalog",
    )
    engine, _repository, registration = _registered_customer(
        tmp_path / "business.sqlite"
    )
    service = DemoCatalogService(project_root=tmp_path, engine=engine)
    result = service.seed(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        scenario_id="single-package-shipping",
    )
    before = SqliteSupportCenterReader(engine).get_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        order_id=result.order_id,
    )
    catalog_path = tmp_path / "data/demo/v1.3/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    target = next(
        product
        for product in catalog["products"]
        if product["product_ref"] == "thermal-travel-mug"
    )
    target["title"] = "目录中的新标题"
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    after = SqliteSupportCenterReader(engine).get_order(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        order_id=result.order_id,
    )
    engine.dispose()

    assert before is not None and after is not None
    assert before.items == after.items
    assert after.items[0].title != "目录中的新标题"
