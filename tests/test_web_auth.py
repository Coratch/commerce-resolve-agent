"""验证邀请注册、认证失败、Session 轮换和退出失效。"""

from tests.conftest import PASSWORD, WebHarness


def test_invitation_register_login_rotates_session_and_logout_revokes_it(
    web_harness: WebHarness,
) -> None:
    """验证完整认证闭环及 Session Fixation 防护。"""

    guest = web_harness.session()
    old_cookie = web_harness.client.cookies.get(
        web_harness.services.settings.cookie_name
    )
    invite = web_harness.repository.create_invitation()
    registered = web_harness.client.post(
        "/api/auth/register",
        headers=web_harness.mutation_headers(str(guest["csrf_token"])),
        json={
            "username": "User.One",
            "password": PASSWORD,
            "invitation_code": invite.code,
        },
    )
    reused = web_harness.client.post(
        "/api/auth/register",
        headers=web_harness.mutation_headers(str(guest["csrf_token"])),
        json={
            "username": "user.two",
            "password": PASSWORD,
            "invitation_code": invite.code,
        },
    )
    logged_in = web_harness.client.post(
        "/api/auth/login",
        headers=web_harness.mutation_headers(str(guest["csrf_token"])),
        json={"username": "USER.ONE", "password": PASSWORD},
    )
    new_cookie = web_harness.client.cookies.get(
        web_harness.services.settings.cookie_name
    )

    assert registered.status_code == 201
    assert reused.status_code == 400
    assert reused.json()["error_code"] == "invitation_unavailable"
    assert web_harness.repository.count_users() == 1
    assert logged_in.status_code == 200
    assert logged_in.json()["mode"] == "registered"
    assert logged_in.json()["username"] == "user.one"
    assert old_cookie != new_cookie
    assert old_cookie is not None
    assert web_harness.repository.resolve_session(old_cookie) is None

    logged_out = web_harness.client.post(
        "/api/auth/logout",
        headers=web_harness.mutation_headers(logged_in.json()["csrf_token"]),
    )
    assert logged_out.status_code == 200
    assert logged_out.json()["mode"] == "guest"
    assert web_harness.client.get("/api/orders").status_code == 401


def test_login_failures_share_one_public_error(
    web_harness: WebHarness,
) -> None:
    """验证不存在账号与密码错误不产生可枚举差异。"""

    logged_in = web_harness.register_and_login("user.one")
    web_harness.client.post(
        "/api/auth/logout",
        headers=web_harness.mutation_headers(str(logged_in["csrf_token"])),
    )
    guest = web_harness.session()
    headers = web_harness.mutation_headers(str(guest["csrf_token"]))

    missing = web_harness.client.post(
        "/api/auth/login",
        headers=headers,
        json={"username": "missing", "password": PASSWORD},
    )
    wrong = web_harness.client.post(
        "/api/auth/login",
        headers=headers,
        json={"username": "user.one", "password": "wrong password"},
    )

    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()
    assert missing.json()["error_code"] == "authentication_failed"


def test_validation_response_never_echoes_password_or_invitation(
    web_harness: WebHarness,
) -> None:
    """验证 Schema 失败详情不会包含原始敏感输入。"""

    session = web_harness.session()
    secret_password = "short-secret"
    secret_invitation = "private-invitation-value"
    response = web_harness.client.post(
        "/api/auth/register",
        headers=web_harness.mutation_headers(str(session["csrf_token"])),
        json={
            "username": "x",
            "password": secret_password,
            "invitation_code": secret_invitation,
        },
    )
    body = response.text

    assert response.status_code == 422
    assert secret_password not in body
    assert secret_invitation not in body
