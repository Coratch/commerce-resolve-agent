"""验证注册用户模型授权、私有查询、conversation 隔离与配额。"""

from unittest.mock import Mock

from commerce_resolve.gateways import InterpreterUnavailableError
from tests.conftest import WebHarness


def _create_order_and_conversation(
    web_harness: WebHarness,
    csrf: str,
) -> str:
    """创建一条私有订单和当前账号绑定的 conversation。"""

    headers = web_harness.mutation_headers(csrf)
    created = web_harness.client.post(
        "/api/orders",
        headers=headers,
        json={
            "order_id": "ORD-PRIVATE",
            "status": "shipped",
            "shipment": {
                "status": "in_transit",
                "last_event": "到达北京分拨中心",
            },
        },
    )
    assert created.status_code == 201
    conversation = web_harness.client.post(
        "/api/conversations",
        headers=headers,
    )
    assert conversation.status_code == 201
    return str(conversation.json()["thread_id"])


def test_registered_chat_uses_llm_interpreter_and_latest_private_data(
    web_harness: WebHarness,
) -> None:
    """验证注册路径调用解释器并在每一轮重新读取最新业务事实。"""

    session = web_harness.register_and_login("user.one")
    csrf = str(session["csrf_token"])
    headers = web_harness.mutation_headers(csrf)
    thread_id = _create_order_and_conversation(web_harness, csrf)
    first = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "查询 ORD-PRIVATE 物流"},
    )
    web_harness.client.patch(
        "/api/orders/ORD-PRIVATE",
        headers=headers,
        json={
            "shipment": {
                "status": "delivered",
                "last_event": "本人已签收",
            }
        },
    )
    second = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "再查一下 ORD-PRIVATE"},
    )

    assert first.status_code == second.status_code == 200
    assert "北京分拨中心" in first.json()["assistant_message"]
    assert "本人已签收" in second.json()["assistant_message"]
    assert web_harness.factory.calls == 2
    assert web_harness.interpreter.calls == [
        "查询 ORD-PRIVATE 物流",
        "再查一下 ORD-PRIVATE",
    ]


def test_registered_chat_rejects_unknown_conversation_and_forged_mode(
    web_harness: WebHarness,
) -> None:
    """验证授权发生在读取 Checkpoint 前且请求不能选择模型模式。"""

    session = web_harness.register_and_login("user.one")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    unknown = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": "00000000-0000-0000-0000-000000000000",
            "message": "查询 ORD-PRIVATE",
        },
    )
    conversation = web_harness.client.post(
        "/api/conversations",
        headers=headers,
    ).json()
    forged = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": conversation["thread_id"],
            "message": "查询 ORD-PRIVATE",
            "interpreter": "fake",
        },
    )

    assert unknown.status_code == 404
    assert forged.status_code == 422
    assert web_harness.factory.calls == 0


def test_registered_chat_enforces_configuration_and_quota_without_fallback(
    web_harness: WebHarness,
) -> None:
    """验证模型未配置和额度耗尽时不会改用游客 Fake 依赖。"""

    session = web_harness.register_and_login("user.one")
    csrf = str(session["csrf_token"])
    headers = web_harness.mutation_headers(csrf)
    thread_id = _create_order_and_conversation(web_harness, csrf)
    web_harness.services.model_configured = False
    unconfigured = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "查询 ORD-PRIVATE"},
    )
    web_harness.services.model_configured = True
    web_harness.services.settings = type(web_harness.services.settings)(
        **{
            **web_harness.services.settings.__dict__,
            "llm_daily_call_limit": 1,
        }
    )
    first = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "查询 ORD-PRIVATE"},
    )
    exhausted = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "再次查询 ORD-PRIVATE"},
    )

    assert unconfigured.status_code == 503
    assert unconfigured.json()["error_code"] == "llm_not_configured"
    assert first.status_code == 200
    assert exhausted.status_code == 429
    assert exhausted.json()["error_code"] == "llm_quota_exceeded"
    assert web_harness.factory.calls == 1


def test_registered_model_failure_is_explicit_and_never_falls_back(
    web_harness: WebHarness,
) -> None:
    """验证上游模型异常返回可重试错误，且不会改用游客 Fake。"""

    session = web_harness.register_and_login("user.one")
    csrf = str(session["csrf_token"])
    thread_id = _create_order_and_conversation(web_harness, csrf)
    interpreter = Mock()
    interpreter.interpret.side_effect = InterpreterUnavailableError("private detail")
    factory = Mock(return_value=interpreter)
    web_harness.services.registered_interpreter_factory = factory

    response = web_harness.client.post(
        "/api/chat/messages",
        headers=web_harness.mutation_headers(csrf),
        json={"thread_id": thread_id, "message": "查询 ORD-PRIVATE"},
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "llm_temporarily_failed"
    assert "private detail" not in response.text
    factory.assert_called_once_with()
    assert web_harness.interpreter.calls == []
