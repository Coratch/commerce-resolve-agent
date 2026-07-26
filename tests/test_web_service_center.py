"""验证售后中心只读 API、身份隔离和客户公开字段。"""

from fastapi.testclient import TestClient

from commerce_resolve.web.app import create_app
from tests.conftest import ORIGIN, PASSWORD, WebHarness


def test_anonymous_support_center_is_not_available(
    web_harness: WebHarness,
) -> None:
    """验证匿名访问者不能读取订单、服务或装配模型。"""

    web_harness.session()
    overview = web_harness.client.get("/api/support/overview")
    orders = web_harness.client.get("/api/support/orders")
    detail = web_harness.client.get("/api/support/orders/CR-7X2P-9K3M")
    services = web_harness.client.get("/api/support/services")

    assert {
        response.status_code for response in (overview, orders, detail, services)
    } == {401}
    assert web_harness.factory.calls == 0
    assert web_harness.interpreter.calls == []


def test_registered_support_center_reads_private_items_and_no_internal_fields(
    web_harness: WebHarness,
) -> None:
    """验证注册客户读取本人商品行，响应不包含内部身份和 Agent 诊断。"""

    web_harness.register_and_login("support.owner")
    web_harness.seed_order(
        "support.owner",
        {
            "order_id": "ORD-SUPPORT",
            "status": "shipped",
            "items": [
                {
                    "sku": "SKU-COAT",
                    "title": "演示外套",
                    "quantity": 1,
                    "product_category": "apparel",
                }
            ],
            "shipment": {
                "status": "in_transit",
                "last_event": "已离开仓库",
            },
        },
    )
    detail = web_harness.client.get("/api/support/orders/ORD-SUPPORT")
    payload = detail.json()

    assert detail.status_code == 200
    assert payload["summary"]["item_count"] == 1
    assert payload["shipment_milestones"][1]["state"] == "current"
    assert payload["items"][0]["title"] == "演示外套"
    serialized = detail.text.lower()
    for internal in (
        "workspace_id",
        "user_id",
        "provider",
        "context_manifest",
        "token",
        "eval",
    ):
        assert internal not in serialized
    assert web_harness.factory.calls == 0


def test_support_order_access_does_not_disclose_other_account(
    web_harness: WebHarness,
) -> None:
    """验证不同账号对同一订单标识使用统一不可访问语义。"""

    web_harness.register_and_login("support.first")
    web_harness.seed_order(
        "support.first",
        {"order_id": "ORD-SECRET", "status": "processing"},
    )
    second = TestClient(
        create_app(services=web_harness.services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    second.get("/api/session")
    invitation = web_harness.repository.create_invitation()
    second.post(
        "/api/auth/register",
        headers=web_harness.mutation_headers(None),
        json={
            "username": "support.second",
            "password": PASSWORD,
            "invitation_code": invitation.code,
        },
    )
    logged_in = second.post(
        "/api/auth/login",
        headers=web_harness.mutation_headers(None),
        json={"username": "support.second", "password": PASSWORD},
    )
    response = second.get("/api/support/orders/ORD-SECRET")
    second.close()

    assert logged_in.status_code == 200
    assert response.status_code == 404
    assert response.json()["error_code"] == "order_not_accessible"


def test_refund_service_projection_is_read_only_and_resumable(
    web_harness: WebHarness,
) -> None:
    """验证待审批退款可从服务列表进入原会话，读取不会再次运行模型。"""

    session = web_harness.register_and_login("support.refund")
    csrf = str(session["csrf_token"])
    headers = web_harness.mutation_headers(csrf)
    web_harness.seed_order(
        "support.refund",
        {"order_id": "ORD-SERVICE", "status": "processing"},
    )
    web_harness.seed_payment(
        "support.refund",
        "ORD-SERVICE",
        {
            "amount": "88.00",
            "currency": "CNY",
            "channel": "mock_wallet",
            "status": "settled",
        },
    )
    conversation = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": "ORD-SERVICE"},
    ).json()
    thread_id = str(conversation["thread_id"])
    web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": thread_id,
            "message": "请退款 ORD-SERVICE，商品有质量问题",
        },
    )
    calls_before = len(web_harness.interpreter.calls)

    services = web_harness.client.get("/api/support/services?view=active")
    service = services.json()["services"][0]
    detail = web_harness.client.get(f"/api/support/services/{service['service_id']}")

    assert services.status_code == detail.status_code == 200
    assert service["status"] == "waiting_user"
    assert service["thread_id"] == thread_id
    assert detail.json()["public_steps"][1]["state"] == "current"
    assert len(web_harness.interpreter.calls) == calls_before
    assert web_harness.services.require_refund_repository().count_refunds() == 0


def test_invalid_support_cursor_uses_public_error(web_harness: WebHarness) -> None:
    """验证伪造分页游标不会泄露解析异常或数据库信息。"""

    web_harness.register_and_login("support.cursor")
    response = web_harness.client.get("/api/support/orders?cursor=not-a-cursor")

    assert response.status_code == 422
    assert response.json() == {
        "error_code": "invalid_cursor",
        "message": "分页位置无效，请刷新后重试。",
    }


def test_order_search_filter_and_cursor_binding_are_server_side(
    web_harness: WebHarness,
) -> None:
    """验证订单搜索、状态筛选和游标绑定均在当前客户作用域内生效。"""

    web_harness.register_and_login("support.search")
    web_harness.seed_order(
        "support.search",
        {
            "order_id": "ORD-SEARCH-A",
            "status": "processing",
            "items": [
                {
                    "sku": "MUG-SEARCH",
                    "title": "搜索专用保温杯",
                    "quantity": 1,
                }
            ],
        },
    )
    web_harness.seed_order(
        "support.search",
        {
            "order_id": "ORD-SEARCH-B",
            "status": "shipped",
            "items": [
                {
                    "sku": "BAG-SEARCH",
                    "title": "搜索专用双肩包",
                    "quantity": 1,
                }
            ],
        },
    )

    by_product = web_harness.client.get(
        "/api/support/orders",
        params={"q": "双肩包"},
    )
    shipping = web_harness.client.get(
        "/api/support/orders",
        params={"view": "shipping"},
    )
    first_page = web_harness.client.get(
        "/api/support/orders",
        params={"limit": 1, "view": "all"},
    )
    cursor = first_page.json()["next_cursor"]
    rebound = web_harness.client.get(
        "/api/support/orders",
        params={"cursor": cursor, "limit": 1, "view": "shipping"},
    )

    assert [item["order_id"] for item in by_product.json()["orders"]] == [
        "ORD-SEARCH-B"
    ]
    assert [item["order_id"] for item in shipping.json()["orders"]] == ["ORD-SEARCH-B"]
    assert cursor is not None
    assert rebound.status_code == 422
    assert rebound.json()["error_code"] == "invalid_cursor"
