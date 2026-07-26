"""验证 Conversation 可信订单绑定和省略订单号处理。"""

from tests.conftest import WebHarness


def _create_bound_conversation(
    web_harness: WebHarness,
    *,
    csrf_token: str,
    order_id: str,
) -> str:
    """创建一条订单和同订单绑定会话并返回 thread ID。"""

    headers = web_harness.mutation_headers(csrf_token)
    web_harness.seed_order(
        web_harness.current_username(),
        {
            "order_id": order_id,
            "status": "shipped",
            "shipment": {
                "status": "in_transit",
                "last_event": "已到达南京转运中心",
            },
        },
    )
    response = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": order_id},
    )
    assert response.status_code == 201
    assert response.json()["related_order_id"] == order_id
    return str(response.json()["thread_id"])


def test_bound_conversation_fills_omitted_order_id(web_harness: WebHarness) -> None:
    """验证订单上下文问题无需重复订单号即可查询权威物流。"""

    session = web_harness.register_and_login("bound.owner")
    csrf = str(session["csrf_token"])
    thread_id = _create_bound_conversation(
        web_harness,
        csrf_token=csrf,
        order_id="ORD-BOUND",
    )
    response = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=web_harness.mutation_headers(csrf),
        json={"client_message_id": "bound-msg-001", "message": "它到哪里了"},
    )
    final = web_harness.latest_public_response(thread_id)

    assert response.status_code == 202
    assert "ORD-BOUND" in final["assistant_message"]
    assert "南京转运中心" in final["assistant_message"]
    summary = web_harness.client.get(f"/api/conversations/{thread_id}").json()
    assert summary["conversation"]["related_order_id"] == "ORD-BOUND"


def test_bound_conversation_rejects_explicit_mismatch_before_model(
    web_harness: WebHarness,
) -> None:
    """验证显式冲突在模型和业务工具前变成普通助手消息。"""

    session = web_harness.register_and_login("bound.guard")
    csrf = str(session["csrf_token"])
    thread_id = _create_bound_conversation(
        web_harness,
        csrf_token=csrf,
        order_id="ORD-BOUND-A",
    )
    calls_before = len(web_harness.interpreter.calls)
    refunds_before = web_harness.services.require_refund_repository().count_refunds()
    response = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=web_harness.mutation_headers(csrf),
        json={
            "client_message_id": "bound-msg-002",
            "message": "查询 ORD-BOUND-B 的物流",
        },
    )
    final = web_harness.latest_public_response(thread_id)

    assert response.status_code == 202
    assert "已绑定其他订单" in final["assistant_message"]
    assert len(web_harness.interpreter.calls) == calls_before
    assert (
        web_harness.services.require_refund_repository().count_refunds()
        == refunds_before
    )


def test_bound_conversation_reuses_active_thread_for_same_order(
    web_harness: WebHarness,
) -> None:
    """验证同订单活动会话在产生消息后仍可恢复且不跨订单复用。"""

    session = web_harness.register_and_login("bound.reuse")
    csrf = str(session["csrf_token"])
    first = _create_bound_conversation(
        web_harness,
        csrf_token=csrf,
        order_id="ORD-REUSE-A",
    )
    headers = web_harness.mutation_headers(csrf)
    repeated = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": "ORD-REUSE-A"},
    )
    web_harness.client.post(
        f"/api/conversations/{first}/messages",
        headers=headers,
        json={"client_message_id": "bound-msg-reuse", "message": "它到哪里了"},
    )
    recovered = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": "ORD-REUSE-A"},
    )
    web_harness.seed_order(
        "bound.reuse",
        {"order_id": "ORD-REUSE-B", "status": "processing"},
    )
    other = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": "ORD-REUSE-B"},
    )

    assert repeated.json()["thread_id"] == first
    assert recovered.json()["thread_id"] == first
    assert other.json()["thread_id"] != first
