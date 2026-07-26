"""验证 Web 退款预览恢复、审批、幂等和安全边界。"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from commerce_resolve.web.app import create_app
from tests.conftest import ORIGIN, WebHarness


def _prepare_refundable_order(
    harness: WebHarness,
    *,
    username: str,
    order_id: str,
) -> tuple[dict[str, object], dict[str, str], str]:
    """注册账号并创建具有 settled Mock 支付的发货前订单和 conversation。"""

    session = harness.register_and_login(username)
    headers = harness.mutation_headers(str(session["csrf_token"]))
    harness.seed_order(
        username,
        {
            "order_id": order_id,
            "status": "processing",
            "shipment": {"status": "preparing", "last_event": "等待揽收"},
        },
    )
    harness.seed_payment(
        username,
        order_id,
        {
            "amount": "129.90",
            "currency": "CNY",
            "channel": "mock_card",
            "status": "settled",
        },
    )
    conversation = harness.create_order_conversation(headers, order_id)
    return session, headers, str(conversation["thread_id"])


def _request_preview(
    harness: WebHarness,
    *,
    headers: dict[str, str],
    thread_id: str,
    order_id: str,
) -> dict[str, object]:
    """通过真实 Web 主图发起退款并返回待审批预览。"""

    response = harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": thread_id,
            "message": f"请退款 {order_id}，商品有质量问题",
        },
    )
    assert response.status_code == 200
    assert response.json()["public_status"] == "refund_awaiting_approval"
    return response.json()["refund_preview"]


def test_pending_refund_survives_request_and_rejects_without_side_effect(
    web_harness: WebHarness,
) -> None:
    """验证新请求可恢复同一预览，拒绝后不写退款且不再次调用模型。"""

    _, headers, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.reject",
        order_id="ORD-REFUND-REJECT",
    )
    preview = _request_preview(
        web_harness,
        headers=headers,
        thread_id=thread_id,
        order_id="ORD-REFUND-REJECT",
    )
    interpreter_calls = len(web_harness.interpreter.calls)

    pending = web_harness.client.get(f"/api/conversations/{thread_id}/pending-refund")
    rejected = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json={"action_id": preview["action_id"], "decision": "reject"},
    )
    result = web_harness.latest_public_response(thread_id)

    assert pending.status_code == 200
    assert pending.json()["refund_preview"] == preview
    assert rejected.status_code == 202
    assert result["public_status"] == "refund_rejected"
    assert web_harness.services.require_refund_repository().count_refunds() == 0
    assert len(web_harness.interpreter.calls) == interpreter_calls


def test_approved_refund_is_verified_and_repeat_approval_is_idempotent(
    web_harness: WebHarness,
) -> None:
    """验证批准产生一笔退款，重复 HTTP 批准返回同一业务结果。"""

    _, headers, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.approve",
        order_id="ORD-REFUND-APPROVE",
    )
    preview = _request_preview(
        web_harness,
        headers=headers,
        thread_id=thread_id,
        order_id="ORD-REFUND-APPROVE",
    )
    body = {"action_id": preview["action_id"], "decision": "approve"}

    approved = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json=body,
    )
    repeated = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json=body,
    )
    result = web_harness.latest_public_response(thread_id)
    events = web_harness.client.get(
        f"/api/conversations/{thread_id}/runs/{approved.json()['run']['run_id']}/events"
    )
    order = web_harness.client.get("/api/support/orders/ORD-REFUND-APPROVE").json()

    assert approved.status_code == repeated.status_code == 202
    assert result["public_status"] == "refund_completed"
    assert result["refund_result"]["verified"] is True
    assert repeated.json()["reused"] is True
    assert repeated.json()["run"]["run_id"] == approved.json()["run"]["run_id"]
    assert "event: step.updated" in events.text
    assert "event: run.completed" in events.text
    assert web_harness.services.require_refund_repository().count_refunds() == 1
    assert web_harness.services.require_refund_repository().count_audit_events() == 4
    assert order["payment"]["status"] == "refunded"
    assert len(order["refunds"]) == 1


def test_changed_order_marks_pending_preview_stale(web_harness: WebHarness) -> None:
    """验证预览后订单变化会使动作失效并阻止旧批准。"""

    _, headers, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.stale",
        order_id="ORD-REFUND-STALE",
    )
    preview = _request_preview(
        web_harness,
        headers=headers,
        thread_id=thread_id,
        order_id="ORD-REFUND-STALE",
    )
    changed = web_harness.update_seeded_order(
        "refund.stale",
        "ORD-REFUND-STALE",
        {
            "status": "shipped",
            "shipment": {"status": "in_transit", "last_event": "已经发货"},
        },
    )
    approved = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json={"action_id": preview["action_id"], "decision": "approve"},
    )

    assert changed.status == "shipped"
    assert approved.status_code == 409
    assert approved.json()["error_code"] == "refund_preview_stale"
    assert web_harness.services.require_refund_repository().count_refunds() == 0


def test_cross_account_and_tampered_approval_have_zero_writes(
    web_harness: WebHarness,
) -> None:
    """验证另一账号和客户端伪造 action 都不能决定退款。"""

    _, headers_a, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.owner",
        order_id="ORD-REFUND-OWNER",
    )
    preview = _request_preview(
        web_harness,
        headers=headers_a,
        thread_id=thread_id,
        order_id="ORD-REFUND-OWNER",
    )
    client_b = TestClient(
        create_app(services=web_harness.services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    original_client = web_harness.client
    web_harness.client = client_b
    try:
        session_b = web_harness.register_and_login("refund.attacker")
        headers_b = web_harness.mutation_headers(str(session_b["csrf_token"]))
        cross_account = client_b.post(
            f"/api/conversations/{thread_id}/refund-approval",
            headers=headers_b,
            json={"action_id": preview["action_id"], "decision": "approve"},
        )
    finally:
        web_harness.client = original_client
        client_b.close()
    tampered = original_client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers_a,
        json={
            "action_id": preview["action_id"],
            "decision": "approve",
            "amount": "0.01",
        },
    )

    assert cross_account.status_code == 404
    assert tampered.status_code == 422
    assert web_harness.services.require_refund_repository().count_refunds() == 0


def test_approval_does_not_consume_second_llm_quota(web_harness: WebHarness) -> None:
    """验证审批恢复从 Checkpoint 继续，不调用 Interpreter 或扣第二次配额。"""

    _, headers, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.quota",
        order_id="ORD-REFUND-QUOTA",
    )
    preview = _request_preview(
        web_harness,
        headers=headers,
        thread_id=thread_id,
        order_id="ORD-REFUND-QUOTA",
    )
    registration = web_harness.repository.authenticate(
        "refund.quota",
        "correct horse battery",
    )
    before = web_harness.repository.get_llm_usage(
        registration.user.id,
        datetime.now(UTC).date(),
    ).accepted_calls

    response = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json={"action_id": preview["action_id"], "decision": "reject"},
    )
    after = web_harness.repository.get_llm_usage(
        registration.user.id,
        datetime.now(UTC).date(),
    ).accepted_calls

    assert response.status_code == 202
    assert before == 1
    assert after == before


def test_pending_thread_blocks_new_messages_before_llm_and_cross_thread_conflicts(
    web_harness: WebHarness,
) -> None:
    """验证待审批会话不再调用模型，同订单请求复用原会话并保持待审批。"""

    _, headers, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.concurrent",
        order_id="ORD-REFUND-CONCURRENT",
    )
    _request_preview(
        web_harness,
        headers=headers,
        thread_id=thread_id,
        order_id="ORD-REFUND-CONCURRENT",
    )
    calls_before = len(web_harness.interpreter.calls)
    blocked = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "再说点别的"},
    )
    second_thread = str(
        web_harness.create_order_conversation(headers, "ORD-REFUND-CONCURRENT")[
            "thread_id"
        ]
    )
    conflicting = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": second_thread,
            "message": "请退款 ORD-REFUND-CONCURRENT，商品有质量问题",
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["error_code"] == "refund_approval_required"
    assert second_thread == thread_id
    assert len(web_harness.interpreter.calls) == calls_before
    assert conflicting.status_code == 409
    assert conflicting.json()["error_code"] == "refund_approval_required"
    assert web_harness.services.require_refund_repository().count_refunds() == 0


def test_refund_approval_enforces_csrf_origin_and_anonymous_boundary(
    web_harness: WebHarness,
) -> None:
    """验证缺少同源审批证明和匿名退款请求都保持零写入。"""

    _, headers, thread_id = _prepare_refundable_order(
        web_harness,
        username="refund.security",
        order_id="ORD-REFUND-SECURITY",
    )
    preview = _request_preview(
        web_harness,
        headers=headers,
        thread_id=thread_id,
        order_id="ORD-REFUND-SECURITY",
    )
    no_origin = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        json={"action_id": preview["action_id"], "decision": "approve"},
    )
    bad_csrf = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers={"Origin": ORIGIN, "X-CSRF-Token": "invalid"},
        json={"action_id": preview["action_id"], "decision": "approve"},
    )
    anonymous_session = web_harness.client.post(
        "/api/auth/logout", headers=headers
    ).json()
    anonymous = web_harness.client.post(
        "/api/conversations",
        headers={"Origin": ORIGIN},
        json={"related_order_id": "ORD-REFUND-SECURITY"},
    )

    assert no_origin.status_code == 403
    assert no_origin.json()["error_code"] == "origin_not_allowed"
    assert bad_csrf.status_code == 403
    assert bad_csrf.json()["error_code"] == "csrf_failed"
    assert anonymous_session["mode"] == "anonymous"
    assert anonymous.status_code == 401
    assert web_harness.services.require_refund_repository().count_refunds() == 0
