"""验证 v2.0 管理员邀请、工作区重置和只读运营边界。"""

from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from tests.conftest import PASSWORD, WebHarness


def _grant_current_admin(
    web_harness: WebHarness,
    username: str,
) -> dict[str, object]:
    """注册账号、可信授予管理员角色并刷新当前 Session。"""

    web_harness.register_and_login(username)
    SqliteAdminRepository(web_harness.repository.engine).set_role(username, "admin")
    refreshed = web_harness.client.get("/api/session")
    assert refreshed.status_code == 200
    return refreshed.json()


def _logout(web_harness: WebHarness, csrf_token: str) -> dict[str, object]:
    """退出当前账号并返回无 Cookie 业务能力的匿名状态。"""

    response = web_harness.client.post(
        "/api/auth/logout",
        headers=web_harness.mutation_headers(csrf_token),
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "anonymous"
    return response.json()


def _login(web_harness: WebHarness, username: str) -> dict[str, object]:
    """从匿名状态登录一个既有账号。"""

    response = web_harness.client.post(
        "/api/auth/login",
        headers=web_harness.mutation_headers(None),
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()


def test_admin_role_is_server_side_and_customer_cannot_use_admin_api(
    web_harness: WebHarness,
) -> None:
    """验证角色来自数据库，普通客户不能靠 URL 或字段越权。"""

    customer = web_harness.register_and_login("customer.one")
    denied = web_harness.client.post(
        "/api/admin/invitations",
        headers=web_harness.mutation_headers(str(customer["csrf_token"])),
        json={"expires_in_hours": 24, "max_uses": 1, "role": "admin"},
    )

    assert customer["role"] == "customer"
    assert customer["capabilities"]["can_access_admin"] is False
    assert web_harness.client.get("/api/admin/overview").status_code == 403
    assert denied.status_code in {403, 422}
    assert web_harness.repository.count_users() == 1


def test_admin_invitation_plaintext_is_returned_only_once(
    web_harness: WebHarness,
) -> None:
    """验证邀请码明文仅在创建响应出现，列表和审计不回显。"""

    admin = _grant_current_admin(web_harness, "admin.invite")
    headers = web_harness.mutation_headers(str(admin["csrf_token"]))
    created = web_harness.client.post(
        "/api/admin/invitations",
        headers=headers,
        json={"expires_in_hours": 24, "max_uses": 2},
    )
    code = created.json()["code"]
    listed = web_harness.client.get("/api/admin/invitations")
    audit = web_harness.client.get("/api/admin/audit")

    assert created.status_code == 201
    assert code not in listed.text
    assert "code_hash" not in listed.text
    assert code not in audit.text
    assert listed.json()[0]["max_uses"] == 2


def test_admin_can_reset_workspace_but_cannot_edit_individual_facts(
    web_harness: WebHarness,
) -> None:
    """验证管理员只能整区重置，旧订单、支付与场景 CRUD 均不可用。"""

    admin = _grant_current_admin(web_harness, "admin.workspace")
    _logout(web_harness, str(admin["csrf_token"]))
    customer = web_harness.register_and_login("workspace.customer")
    original_ids = {
        item["order_id"]
        for item in web_harness.client.get("/api/support/orders").json()["orders"]
    }
    target = next(
        item
        for item in web_harness.services.require_admin_repository().list_customers()
        if item.username == "workspace.customer"
    )
    _logout(web_harness, str(customer["csrf_token"]))
    admin = _login(web_harness, "admin.workspace")
    headers = web_harness.mutation_headers(str(admin["csrf_token"]))

    legacy_endpoints = (
        web_harness.client.post(
            f"/api/admin/customers/{target.user_id}/orders",
            headers=headers,
            json={"order_id": "ORD-DENIED", "status": "processing"},
        ),
        web_harness.client.put(
            f"/api/admin/customers/{target.user_id}/orders/ORD-DENIED/payment",
            headers=headers,
            json={
                "amount": "10.00",
                "currency": "CNY",
                "channel": "mock_card",
                "status": "settled",
            },
        ),
        web_harness.client.post(
            f"/api/admin/customers/{target.user_id}/demo-scenarios",
            headers=headers,
            json={"scenario_id": "legacy", "client_request_id": "legacy"},
        ),
    )
    reset = web_harness.client.post(
        f"/api/admin/customers/{target.user_id}/demo-workspace/reset",
        headers=headers,
        json={"client_request_id": "admin-reset-1", "confirmation": "RESET"},
    )

    assert {response.status_code for response in legacy_endpoints} == {404}
    assert reset.status_code == 200
    assert set(reset.json()["order_ids"]) == original_ids
    assert reset.json()["dataset_version"] == "portfolio-demo-v1"
    audit = web_harness.client.get("/api/admin/audit")
    assert "workspace" in audit.text


def test_admin_workspace_reset_is_idempotent(
    web_harness: WebHarness,
) -> None:
    """验证相同请求标识只完成一次重置代次和一份业务结果。"""

    admin = _grant_current_admin(web_harness, "admin.reset")
    target = next(
        item
        for item in web_harness.services.require_admin_repository().list_customers()
        if item.username == "admin.reset"
    )
    headers = web_harness.mutation_headers(str(admin["csrf_token"]))
    endpoint = f"/api/admin/customers/{target.user_id}/demo-workspace/reset"
    body = {"client_request_id": "same-reset", "confirmation": "RESET"}

    first = web_harness.client.post(endpoint, headers=headers, json=body)
    repeated = web_harness.client.post(endpoint, headers=headers, json=body)

    assert first.status_code == repeated.status_code == 200
    assert first.json()["reset_generation"] == repeated.json()["reset_generation"]
    assert repeated.json()["already_completed"] is True
    assert first.json()["order_ids"] == repeated.json()["order_ids"]


def test_admin_monitoring_eval_and_system_are_read_only_and_sanitized(
    web_harness: WebHarness,
) -> None:
    """验证运营读取不泄露消息正文，也不补造 Eval 或任务数据。"""

    admin = _grant_current_admin(web_harness, "admin.monitor")
    headers = web_harness.mutation_headers(str(admin["csrf_token"]))
    thread_id = str(web_harness.create_order_conversation(headers)["thread_id"])
    accepted = (
        web_harness.services.require_conversation_repository().accept_chat_message(
            thread_id=thread_id,
            subject_id=next(
                item.user_id
                for item in SqliteAdminRepository(
                    web_harness.repository.engine
                ).list_customers()
                if item.username == "admin.monitor"
            ),
            workspace_id=next(
                item.workspace_id
                for item in SqliteAdminRepository(
                    web_harness.repository.engine
                ).list_customers()
                if item.username == "admin.monitor"
            ),
            access_mode="registered",
            client_request_id="admin-monitoring-test",
            message="这是不能出现在 Monitoring 响应中的完整客户消息",
        )
    )

    before = web_harness.services.require_admin_repository().overview_counts()
    runs = web_harness.client.get("/api/admin/agent-runs")
    detail = web_harness.client.get(f"/api/admin/agent-runs/{accepted.run.run_id}")
    evaluation = web_harness.client.get("/api/admin/eval")
    system = web_harness.client.get("/api/admin/system")
    after = web_harness.services.require_admin_repository().overview_counts()

    assert runs.status_code == detail.status_code == 200
    assert "完整客户消息" not in runs.text + detail.text
    assert "request_hash" not in runs.text + detail.text
    assert evaluation.json()["state"] == "missing"
    assert system.json()["version"]
    assert "/Users/" not in system.text
    assert before == after
