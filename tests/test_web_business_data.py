"""验证私有订单物流 CRUD、公开响应和跨账号隔离。"""

from fastapi.testclient import TestClient

from commerce_resolve.web.app import create_app
from tests.conftest import ORIGIN, WebHarness


def test_registered_user_can_manage_private_orders(
    web_harness: WebHarness,
) -> None:
    """验证创建、列出、更新和删除都读取当前 Session 工作区。"""

    session = web_harness.register_and_login("user.one")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    created = web_harness.client.post(
        "/api/orders",
        headers=headers,
        json={
            "order_id": "ORD-PRIVATE",
            "status": "processing",
            "shipment": {
                "status": "preparing",
                "last_event": "等待揽收",
            },
        },
    )
    listed = web_harness.client.get("/api/orders")
    updated = web_harness.client.patch(
        "/api/orders/ORD-PRIVATE",
        headers=headers,
        json={
            "status": "shipped",
            "shipment": {
                "status": "in_transit",
                "last_event": "已离开杭州仓",
            },
        },
    )
    deleted = web_harness.client.delete(
        "/api/orders/ORD-PRIVATE",
        headers=headers,
    )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert len(listed.json()["orders"]) == 1
    assert updated.json()["status"] == "shipped"
    assert updated.json()["shipment"]["last_event"] == "已离开杭州仓"
    assert deleted.json() == {"deleted": True}
    assert web_harness.client.get("/api/orders").json()["orders"] == []
    for internal in ("user_id", "workspace_id"):
        assert internal not in created.json()


def test_accounts_with_same_order_id_are_isolated(
    web_harness: WebHarness,
) -> None:
    """验证相同订单号按私有工作区隔离且不能跨账号修改。"""

    session_a = web_harness.register_and_login("user.a")
    headers_a = web_harness.mutation_headers(str(session_a["csrf_token"]))
    web_harness.client.post(
        "/api/orders",
        headers=headers_a,
        json={"order_id": "ORD-SAME", "status": "processing"},
    )
    client_b = TestClient(
        create_app(services=web_harness.services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    original_client = web_harness.client
    web_harness.client = client_b
    try:
        session_b = web_harness.register_and_login("user.b")
        headers_b = web_harness.mutation_headers(str(session_b["csrf_token"]))
        created_b = client_b.post(
            "/api/orders",
            headers=headers_b,
            json={"order_id": "ORD-SAME", "status": "delivered"},
        )
        assert created_b.status_code == 201
        assert client_b.get("/api/orders").json()["orders"][0]["status"] == "delivered"
    finally:
        web_harness.client = original_client
        client_b.close()

    order_a = original_client.get("/api/orders").json()["orders"][0]
    assert order_a["status"] == "processing"


def test_order_requests_reject_client_workspace_fields(
    web_harness: WebHarness,
) -> None:
    """验证客户端不能在业务写入中选择用户或工作区。"""

    session = web_harness.register_and_login("user.one")
    response = web_harness.client.post(
        "/api/orders",
        headers=web_harness.mutation_headers(str(session["csrf_token"])),
        json={
            "order_id": "ORD-FORGED",
            "status": "processing",
            "workspace_id": "other",
            "user_id": "admin",
        },
    )

    assert response.status_code == 422
    assert web_harness.client.get("/api/orders").json()["orders"] == []


def test_registered_user_can_manage_mock_payment(web_harness: WebHarness) -> None:
    """验证注册用户可维护本工作区支付，并在订单响应中读取交易摘要。"""

    session = web_harness.register_and_login("payment.owner")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    web_harness.client.post(
        "/api/orders",
        headers=headers,
        json={"order_id": "ORD-PAYMENT", "status": "processing"},
    )

    payment = web_harness.client.put(
        "/api/orders/ORD-PAYMENT/payment",
        headers=headers,
        json={
            "amount": "129.90",
            "currency": "CNY",
            "channel": "mock_wallet",
            "status": "settled",
        },
    )
    listed = web_harness.client.get("/api/orders").json()["orders"][0]

    assert payment.status_code == 200
    assert payment.json()["amount"] == "129.90"
    assert listed["payment"]["channel"] == "mock_wallet"
    assert listed["refunds"] == []
    assert (
        web_harness.client.delete(
            "/api/orders/ORD-PAYMENT",
            headers=headers,
        ).status_code
        == 409
    )
