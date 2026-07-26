"""验证 v0.6 公开会话历史、Run、SSE 与生命周期。"""

from uuid import uuid4

from tests.conftest import WebHarness


def _registered_conversation(
    web_harness: WebHarness,
) -> tuple[str, dict[str, str], str]:
    """创建注册用户、私有订单和活动会话，返回 thread、Header 与订单号。"""

    username = f"v06.{uuid4().hex[:8]}"
    session = web_harness.register_and_login(username)
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    order_id = f"ORD-{uuid4().hex[:8].upper()}"
    web_harness.seed_order(
        username,
        {
            "order_id": order_id,
            "status": "shipped",
            "shipment": {
                "status": "in_transit",
                "last_event": "到达杭州分拨中心",
            },
        },
    )
    conversation = web_harness.create_order_conversation(headers, order_id)
    return str(conversation["thread_id"]), headers, order_id


def test_async_run_persists_messages_and_replays_sse(
    web_harness: WebHarness,
) -> None:
    """验证 202 Run 最终产生公开历史并可从事件 0 完整重放。"""

    thread_id, headers, order_id = _registered_conversation(web_harness)
    accepted = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=headers,
        json={
            "client_message_id": "client-message-0001",
            "message": f"查询 {order_id} 物流",
        },
    )

    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["run_id"]
    run = web_harness.client.get(f"/api/conversations/{thread_id}/runs/{run_id}")
    history = web_harness.client.get(f"/api/conversations/{thread_id}/messages")
    events = web_harness.client.get(
        f"/api/conversations/{thread_id}/runs/{run_id}/events"
    )

    assert run.status_code == 200
    assert run.json()["run"]["status"] == "completed"
    assert "request_hash" not in accepted.json()["run"]
    assert "checkpoint_id" not in accepted.json()["run"]
    assert "request_hash" not in run.json()["run"]
    assert "checkpoint_id" not in run.json()["run"]
    assert [item["role"] for item in history.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert "杭州分拨中心" in history.json()["messages"][1]["content"]
    assert "event: run.accepted" in events.text
    assert "event: step.updated" in events.text
    assert "event: message.completed" in events.text
    assert "event: run.completed" in events.text
    assert "event_key" not in events.text


def test_client_request_id_is_idempotent_and_payload_bound(
    web_harness: WebHarness,
) -> None:
    """验证相同请求只复用 Run，改变正文则返回冲突。"""

    thread_id, headers, _ = _registered_conversation(web_harness)
    payload = {
        "client_message_id": "client-message-idempotent",
        "message": "普通商品退货期限是几天？",
    }
    first = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=headers,
        json=payload,
    )
    repeated = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=headers,
        json=payload,
    )
    conflict = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=headers,
        json={**payload, "message": "换一个问题"},
    )
    history = web_harness.client.get(f"/api/conversations/{thread_id}/messages")

    assert first.status_code == repeated.status_code == 202
    assert repeated.json()["reused"] is True
    assert repeated.json()["run"]["run_id"] == first.json()["run"]["run_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "client_request_conflict"
    assert len(history.json()["messages"]) == 2


def test_async_quota_failure_persists_clear_recovery_time(
    web_harness: WebHarness,
) -> None:
    """验证后台 Run 达到额度后以普通客服消息说明上限和恢复时间。"""

    thread_id, headers, order_id = _registered_conversation(web_harness)
    web_harness.services.settings = type(web_harness.services.settings)(
        **{
            **web_harness.services.settings.__dict__,
            "llm_daily_call_limit": 1,
        }
    )
    first = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=headers,
        json={
            "client_message_id": "quota-message-0001",
            "message": f"查询 {order_id} 物流",
        },
    )
    exhausted = web_harness.client.post(
        f"/api/conversations/{thread_id}/messages",
        headers=headers,
        json={
            "client_message_id": "quota-message-0002",
            "message": f"再次查询 {order_id} 物流",
        },
    )
    history = web_harness.client.get(f"/api/conversations/{thread_id}/messages")
    latest = history.json()["messages"][-1]

    assert first.status_code == exhausted.status_code == 202
    assert latest["status"] == "failed"
    assert "今日对话次数已用完（1次），将于" in latest["content"]
    assert latest["content"].endswith("恢复。")


def test_conversation_list_archive_and_delete_are_owner_scoped(
    web_harness: WebHarness,
) -> None:
    """验证注册用户可归档和删除本人会话，删除后 URL 不再公开。"""

    thread_id, headers, _ = _registered_conversation(web_harness)
    active = web_harness.client.get("/api/conversations")
    archived = web_harness.client.patch(
        f"/api/conversations/{thread_id}",
        headers=headers,
        json={"lifecycle_status": "archived"},
    )
    archived_list = web_harness.client.get(
        "/api/conversations?lifecycle_status=archived"
    )
    restored = web_harness.client.patch(
        f"/api/conversations/{thread_id}",
        headers=headers,
        json={"lifecycle_status": "active"},
    )
    deleted = web_harness.client.delete(
        f"/api/conversations/{thread_id}", headers=headers
    )
    missing = web_harness.client.get(f"/api/conversations/{thread_id}")

    assert thread_id in {item["thread_id"] for item in active.json()["conversations"]}
    assert archived.status_code == restored.status_code == 200
    assert archived.json()["conversation"]["lifecycle_status"] == "archived"
    assert thread_id in {
        item["thread_id"] for item in archived_list.json()["conversations"]
    }
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_pending_checkpoint_blocks_archive_and_delete(
    web_harness: WebHarness,
) -> None:
    """验证持久中断未处理时不能归档或删除会话。"""

    thread_id, headers, _ = _registered_conversation(web_harness)
    pending = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": thread_id,
            "message": "请升级二线客服处理这个复杂售后问题",
        },
    )
    archived = web_harness.client.patch(
        f"/api/conversations/{thread_id}",
        headers=headers,
        json={"lifecycle_status": "archived"},
    )
    deleted = web_harness.client.delete(
        f"/api/conversations/{thread_id}",
        headers=headers,
    )

    assert pending.status_code == 200
    assert pending.json()["public_status"] == "l2_awaiting_confirmation"
    assert archived.status_code == deleted.status_code == 409
    assert archived.json()["error_code"] == "pending_action_required"
    assert deleted.json()["error_code"] == "pending_action_required"
