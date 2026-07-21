"""验证游客 Session、只读 Chat、零 LLM 和请求伪造防护。"""

from tests.conftest import WebHarness


def test_guest_can_query_demo_order_and_policy_without_llm(
    web_harness: WebHarness,
) -> None:
    """验证游客订单与政策查询只使用 Fake Interpreter。"""

    session = web_harness.session()
    csrf = str(session["csrf_token"])
    assert session["mode"] == "guest"
    assert session["capabilities"]["can_use_llm"] is False
    assert "session_token" not in session
    conversation = web_harness.client.post(
        "/api/conversations",
        headers=web_harness.mutation_headers(csrf),
    )
    thread_id = conversation.json()["thread_id"]

    order = web_harness.client.post(
        "/api/chat/messages",
        headers=web_harness.mutation_headers(csrf),
        json={"thread_id": thread_id, "message": "帮我查询 ORD-001 的物流"},
    )
    policy = web_harness.client.post(
        "/api/chat/messages",
        headers=web_harness.mutation_headers(csrf),
        json={"thread_id": thread_id, "message": "普通商品退货期限是几天"},
    )

    assert order.status_code == 200
    assert order.json()["public_status"] == "completed"
    assert "上海转运中心" in order.json()["assistant_message"]
    assert policy.status_code == 200
    assert policy.json()["public_status"] == "policy_answered"
    assert policy.json()["citations"]
    assert web_harness.factory.calls == 0
    assert web_harness.interpreter.calls == []


def test_guest_cannot_write_or_forge_access_fields(
    web_harness: WebHarness,
) -> None:
    """验证游客写入无副作用，Chat 额外权限字段被 Schema 拒绝。"""

    session = web_harness.session()
    csrf = str(session["csrf_token"])
    denied = web_harness.client.post(
        "/api/orders",
        headers=web_harness.mutation_headers(csrf),
        json={"order_id": "ORD-GUEST", "status": "processing"},
    )
    conversation = web_harness.client.post(
        "/api/conversations",
        headers=web_harness.mutation_headers(csrf),
    ).json()
    forged = web_harness.client.post(
        "/api/chat/messages",
        headers=web_harness.mutation_headers(csrf),
        json={
            "thread_id": conversation["thread_id"],
            "message": "查询 ORD-001",
            "workspace_id": "private",
            "user_id": "admin",
            "interpreter": "openai",
        },
    )

    assert denied.status_code == 401
    assert forged.status_code == 422
    assert web_harness.repository.count_users() == 0
    assert web_harness.factory.calls == 0


def test_mutations_require_origin_and_current_csrf(
    web_harness: WebHarness,
) -> None:
    """验证缺少同源证明或同步 Token 时不会创建 conversation。"""

    session = web_harness.session()
    csrf = str(session["csrf_token"])
    no_origin = web_harness.client.post(
        "/api/conversations",
        headers={"X-CSRF-Token": csrf},
    )
    wrong_csrf = web_harness.client.post(
        "/api/conversations",
        headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf},
    )

    assert no_origin.status_code == 403
    assert no_origin.json()["error_code"] == "origin_not_allowed"
    assert wrong_csrf.status_code == 403
    assert wrong_csrf.json()["error_code"] == "origin_not_allowed"
    assert no_origin.headers["x-content-type-options"] == "nosniff"
