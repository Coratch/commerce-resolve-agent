"""验证 Web Session、conversation 与 LangGraph State 的跨实例恢复。"""

from fastapi.testclient import TestClient

from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
)
from commerce_resolve.adapters.sqlite_policy import SqlitePolicyRepository
from commerce_resolve.web.app import create_app
from commerce_resolve.web.dependencies import (
    InMemoryRateLimiter,
    ThreadLockRegistry,
    WebServices,
)
from tests.conftest import ORIGIN, InterpreterFactory, WebHarness


def test_registered_conversation_resumes_after_new_app_instance(
    web_harness: WebHarness,
) -> None:
    """验证新 Engine 和新 App 可复用 Session、thread 与 Checkpoint。"""

    session = web_harness.register_and_login("user.owner")
    csrf = str(session["csrf_token"])
    headers = web_harness.mutation_headers(csrf)
    web_harness.seed_order(
        "user.owner",
        {
            "order_id": "ORD-PRIVATE",
            "status": "shipped",
            "shipment": {
                "status": "in_transit",
                "last_event": "跨实例恢复成功",
            },
        },
    )
    thread_id = web_harness.create_order_conversation(headers, "ORD-PRIVATE")[
        "thread_id"
    ]
    first = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "帮我查一下物流"},
    )
    token = web_harness.client.cookies.get(web_harness.services.settings.cookie_name)
    assert first.json()["public_status"] == "completed"
    assert token is not None

    web_harness.client.close()
    web_harness.repository.engine.dispose()
    engine = create_business_engine(web_harness.services.settings.business_db_path)
    repository = SqliteBusinessRepository(engine)
    factory = InterpreterFactory(web_harness.interpreter)
    services = WebServices(
        settings=web_harness.services.settings,
        repository=repository,
        policy_repository=SqlitePolicyRepository(
            web_harness.services.settings.policy_index_db_path,
            source_root=web_harness.services.settings.policy_source_path,
        ),
        registered_interpreter_factory=factory,
        model_configured=True,
    )
    client = TestClient(
        create_app(services=services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    try:
        client.cookies.set(services.settings.cookie_name, token)
        restored = client.get("/api/session")
        second = client.post(
            "/api/chat/messages",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": restored.json()["csrf_token"],
            },
            json={"thread_id": thread_id, "message": "ORD-PRIVATE"},
        )

        assert restored.json()["mode"] == "registered"
        assert second.status_code == 200
        assert second.json()["public_status"] == "completed"
        assert "跨实例恢复成功" in second.json()["assistant_message"]
    finally:
        client.close()
        engine.dispose()


def test_pending_refund_resumes_and_executes_after_new_app_instance(
    web_harness: WebHarness,
) -> None:
    """验证重建 Engine 和 App 后仍能恢复同一预览且不重复调用模型。"""

    session = web_harness.register_and_login("refund.restart")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    web_harness.seed_order(
        "refund.restart",
        {
            "order_id": "ORD-REFUND-RESTART",
            "status": "processing",
            "shipment": {"status": "preparing", "last_event": "等待揽收"},
        },
    )
    web_harness.seed_payment(
        "refund.restart",
        "ORD-REFUND-RESTART",
        {
            "amount": "129.90",
            "currency": "CNY",
            "channel": "mock_card",
            "status": "settled",
        },
    )
    thread_id = web_harness.create_order_conversation(headers, "ORD-REFUND-RESTART")[
        "thread_id"
    ]
    first = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": thread_id,
            "message": "请退款 ORD-REFUND-RESTART，商品有质量问题",
        },
    )
    preview = first.json()["refund_preview"]
    token = web_harness.client.cookies.get(web_harness.services.settings.cookie_name)
    interpreter_calls = len(web_harness.interpreter.calls)
    assert first.json()["public_status"] == "refund_awaiting_approval"
    assert token is not None

    web_harness.client.close()
    web_harness.repository.engine.dispose()
    engine = create_business_engine(web_harness.services.settings.business_db_path)
    repository = SqliteBusinessRepository(engine)
    services = WebServices(
        settings=web_harness.services.settings,
        repository=repository,
        policy_repository=SqlitePolicyRepository(
            web_harness.services.settings.policy_index_db_path,
            source_root=web_harness.services.settings.policy_source_path,
        ),
        registered_interpreter_factory=InterpreterFactory(web_harness.interpreter),
        model_configured=True,
    )
    client = TestClient(
        create_app(services=services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    try:
        client.cookies.set(services.settings.cookie_name, token)
        restored_session = client.get("/api/session").json()
        pending = client.get(f"/api/conversations/{thread_id}/pending-refund")
        approved = client.post(
            f"/api/conversations/{thread_id}/refund-approval",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": restored_session["csrf_token"],
            },
            json={"action_id": preview["action_id"], "decision": "approve"},
        )
        history = client.get(f"/api/conversations/{thread_id}/messages").json()
        latest = next(
            item
            for item in reversed(history["messages"])
            if item["role"] == "assistant"
        )

        assert pending.status_code == 200
        assert pending.json()["refund_preview"] == preview
        assert approved.status_code == 202
        assert latest["payload"]["public_status"] == "refund_completed"
        assert services.require_refund_repository().count_refunds() == 1
        assert len(web_harness.interpreter.calls) == interpreter_calls
    finally:
        client.close()
        engine.dispose()


def test_thread_lock_and_rate_limit_are_deterministic() -> None:
    """验证同 thread 并发拒绝和固定窗口限流不依赖模型判断。"""

    registry = ThreadLockRegistry()
    with registry.acquire("thread-1") as first:
        with registry.acquire("thread-1") as second:
            assert first is True
            assert second is False
    with registry.acquire("thread-1") as released:
        assert released is True

    limiter = InMemoryRateLimiter(now_provider=MockClock())
    assert limiter.allow("login:client", limit=2) is True
    assert limiter.allow("login:client", limit=2) is True
    assert limiter.allow("login:client", limit=2) is False


class MockClock:
    """为限流单元测试提供固定单调时间。"""

    def __call__(self) -> float:
        """返回不会自动推进的时间值。"""

        return 0.0
