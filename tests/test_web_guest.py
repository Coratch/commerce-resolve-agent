"""验证 v2.0 匿名访问零业务、零模型和同源写入边界。"""

from tests.conftest import WebHarness


def test_anonymous_session_has_no_cookie_or_business_capability(
    web_harness: WebHarness,
) -> None:
    """验证匿名状态不创建 Cookie、工作区、会话或模型依赖。"""

    session = web_harness.session()
    endpoints = (
        "/api/support/overview",
        "/api/support/orders",
        "/api/support/services",
        "/api/conversations",
        "/api/memories",
    )
    responses = [web_harness.client.get(path) for path in endpoints]

    assert session["mode"] == "anonymous"
    assert session["session_scope"] == "none"
    assert session["csrf_token"] is None
    assert all(value is False for value in session["capabilities"].values())
    assert (
        web_harness.client.cookies.get(web_harness.services.settings.cookie_name)
        is None
    )
    assert {response.status_code for response in responses} == {401}
    assert web_harness.repository.count_users() == 0
    assert web_harness.factory.calls == 0
    assert web_harness.interpreter.calls == []


def test_anonymous_cannot_create_conversation_or_forge_identity(
    web_harness: WebHarness,
) -> None:
    """验证匿名请求和伪造身份字段均不会建立业务任务。"""

    denied = web_harness.client.post(
        "/api/conversations",
        headers=web_harness.mutation_headers(None),
        json={
            "related_order_id": "CR-7X2P-9K3M",
            "workspace_id": "forged",
            "user_id": "admin",
        },
    )

    assert denied.status_code in {401, 422}
    assert web_harness.repository.count_users() == 0
    assert web_harness.factory.calls == 0


def test_anonymous_public_mutations_require_trusted_origin(
    web_harness: WebHarness,
) -> None:
    """验证登录与注册虽不需要 CSRF，但仍拒绝不可信 Origin。"""

    no_origin = web_harness.client.post(
        "/api/auth/login",
        json={"username": "missing", "password": "not-a-valid-password"},
    )
    wrong_origin = web_harness.client.post(
        "/api/auth/login",
        headers={"Origin": "http://evil.example"},
        json={"username": "missing", "password": "not-a-valid-password"},
    )

    assert no_origin.status_code == wrong_origin.status_code == 403
    assert no_origin.json()["error_code"] == "origin_not_allowed"
    assert wrong_origin.json()["error_code"] == "origin_not_allowed"
    assert no_origin.headers["x-content-type-options"] == "nosniff"
