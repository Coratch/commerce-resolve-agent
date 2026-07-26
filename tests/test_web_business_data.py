"""验证 v1.2 客户只读订单契约和账号间业务数据隔离。"""

from fastapi.testclient import TestClient

from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.business_models import OrderCreate
from commerce_resolve.web.app import create_app
from tests.conftest import ORIGIN, WebHarness


def test_registered_customer_cannot_mutate_mock_business_data(
    web_harness: WebHarness,
) -> None:
    """验证普通客户不能再创建、修改、删除订单或维护 Mock 支付。"""

    session = web_harness.register_and_login("customer.readonly")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    requests = (
        web_harness.client.post(
            "/api/orders",
            headers=headers,
            json={"order_id": "ORD-DENIED", "status": "processing"},
        ),
        web_harness.client.patch(
            "/api/orders/ORD-DENIED",
            headers=headers,
            json={"status": "shipped"},
        ),
        web_harness.client.put(
            "/api/orders/ORD-DENIED/payment",
            headers=headers,
            json={
                "amount": "99.00",
                "currency": "CNY",
                "channel": "mock_wallet",
                "status": "settled",
            },
        ),
        web_harness.client.delete("/api/orders/ORD-DENIED", headers=headers),
    )

    assert {response.status_code for response in requests} == {404}
    support_orders = web_harness.client.get("/api/support/orders")
    assert support_orders.status_code == 200
    assert len(support_orders.json()["orders"]) == 3


def test_customer_order_reads_remain_isolated_after_admin_data_migration(
    web_harness: WebHarness,
) -> None:
    """验证管理员写入的同号订单仍按客户工作区隔离读取。"""

    web_harness.register_and_login("customer.a")
    client_b = TestClient(
        create_app(services=web_harness.services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    original_client = web_harness.client
    web_harness.client = client_b
    try:
        web_harness.register_and_login("customer.b")
    finally:
        web_harness.client = original_client

    customers = SqliteAdminRepository(web_harness.repository.engine).list_customers()
    customer_a = next(item for item in customers if item.username == "customer.a")
    customer_b = next(item for item in customers if item.username == "customer.b")
    web_harness.repository.create_order(
        user_id=customer_a.user_id,
        workspace_id=customer_a.workspace_id,
        data=OrderCreate(order_id="ORD-SAME", status="processing"),
    )
    web_harness.repository.create_order(
        user_id=customer_b.user_id,
        workspace_id=customer_b.workspace_id,
        data=OrderCreate(order_id="ORD-SAME", status="delivered"),
    )

    try:
        order_a = original_client.get("/api/support/orders/ORD-SAME")
        order_b = client_b.get("/api/support/orders/ORD-SAME")
        assert order_a.json()["summary"]["status"] == "processing"
        assert order_b.json()["summary"]["status"] == "delivered"
    finally:
        client_b.close()
