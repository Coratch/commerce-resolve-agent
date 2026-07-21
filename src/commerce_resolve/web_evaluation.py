"""运行 v0.3 Web、身份、隔离、模型授权与恢复的离线 Eval。"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from commerce_resolve.adapters.fake import FakeQueryInterpreter
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.gateways import QueryInterpreter
from commerce_resolve.models import Interpretation, InterpretationContext
from commerce_resolve.web.app import create_app
from commerce_resolve.web.dependencies import WebServices
from commerce_resolve.web.settings import WebSettings

V03EvalCategory = Literal[
    "guest",
    "invitation_auth",
    "private_data",
    "registered_llm",
    "recovery",
]
SecurityMetric = Literal[
    "guest_llm_call",
    "unauthorized_write",
    "forgery_success",
    "invitation_overconsumption",
    "cross_user_leak",
    "credential_leak",
]

ORIGIN = "http://testserver"
PASSWORD = "eval correct horse battery"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_SOURCE = PROJECT_ROOT / "data" / "policies"


class V03EvalScenario(BaseModel):
    """定义固定 Web 场景及失败时对应的安全指标。"""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    category: V03EvalCategory
    security_metric: SecurityMetric | None = None


class V03EvalScenarioResult(BaseModel):
    """保存单个场景是否通过及脱敏失败类型。"""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    category: V03EvalCategory
    passed: bool
    security_violations: tuple[SecurityMetric, ...] = ()
    error_type: str | None = None


class V03EvalReport(BaseModel):
    """汇总 v0.3 固定场景和全部发布安全门槛。"""

    model_config = ConfigDict(frozen=True)

    suite: str
    total_scenarios: int
    passed_scenarios: int
    guest_llm_calls: int
    unauthorized_business_writes: int
    forgery_successes: int
    invitation_overconsumption: int
    cross_user_leaks: int
    credential_leaks: int
    passed: bool
    category_counts: dict[str, int]
    results: tuple[V03EvalScenarioResult, ...]


SCENARIOS = (
    V03EvalScenario(
        scenario_id="guest-order",
        category="guest",
        security_metric="guest_llm_call",
    ),
    V03EvalScenario(
        scenario_id="guest-logistics",
        category="guest",
        security_metric="guest_llm_call",
    ),
    V03EvalScenario(
        scenario_id="guest-policy",
        category="guest",
        security_metric="guest_llm_call",
    ),
    V03EvalScenario(
        scenario_id="guest-write-denied",
        category="guest",
        security_metric="unauthorized_write",
    ),
    V03EvalScenario(scenario_id="invite-valid", category="invitation_auth"),
    V03EvalScenario(scenario_id="invite-unavailable", category="invitation_auth"),
    V03EvalScenario(
        scenario_id="invite-repeat",
        category="invitation_auth",
        security_metric="invitation_overconsumption",
    ),
    V03EvalScenario(scenario_id="login-logout", category="invitation_auth"),
    V03EvalScenario(scenario_id="session-expired", category="invitation_auth"),
    V03EvalScenario(scenario_id="private-create-list", category="private_data"),
    V03EvalScenario(scenario_id="private-update-delete", category="private_data"),
    V03EvalScenario(scenario_id="private-same-order", category="private_data"),
    V03EvalScenario(scenario_id="private-invalid-schema", category="private_data"),
    V03EvalScenario(
        scenario_id="private-cross-user",
        category="private_data",
        security_metric="cross_user_leak",
    ),
    V03EvalScenario(scenario_id="llm-private-order", category="registered_llm"),
    V03EvalScenario(scenario_id="llm-policy", category="registered_llm"),
    V03EvalScenario(
        scenario_id="llm-forged-mode",
        category="registered_llm",
        security_metric="forgery_success",
    ),
    V03EvalScenario(scenario_id="llm-quota", category="registered_llm"),
    V03EvalScenario(scenario_id="recovery-cross-instance", category="recovery"),
    V03EvalScenario(
        scenario_id="recovery-identity-mismatch",
        category="recovery",
        security_metric="cross_user_leak",
    ),
)


class _EvalClock:
    """提供场景可推进的固定 UTC 时钟。"""

    def __init__(self) -> None:
        """从当前 UTC 时间开始，避免浏览器立即丢弃已过期的测试 Cookie。"""

        self.value = datetime.now(UTC)

    def __call__(self) -> datetime:
        """返回当前测试时间。"""

        return self.value


class _EvalInterpreter:
    """模拟注册用户 LLM 的结构化输出并记录真实调用次数。"""

    def __init__(self) -> None:
        """初始化调用记录和政策语义委托。"""

        self.calls: list[str] = []
        self._policy = FakeQueryInterpreter()

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """识别任意合法演示订单号，其余政策查询使用确定性规则。"""

        self.calls.append(text)
        match = re.search(r"\bORD-[A-Z0-9-]{3,32}\b", text, re.IGNORECASE)
        if match is not None:
            return Interpretation(
                intent="order_inquiry",
                order_id=match.group(0).upper(),
            )
        return self._policy.interpret(text, context)


class _EvalInterpreterFactory:
    """记录注册路径依赖装配并返回同一个离线解释器。"""

    def __init__(self, interpreter: QueryInterpreter) -> None:
        """保存待注入解释器。"""

        self.interpreter = interpreter
        self.calls = 0

    def __call__(self) -> QueryInterpreter:
        """记录一次真实模型路径选择并返回离线替身。"""

        self.calls += 1
        return self.interpreter


class _ScenarioRuntime:
    """为单个 Eval 场景管理独立数据库、应用和浏览器客户端。"""

    def __init__(self, root: Path, *, llm_limit: int = 20) -> None:
        """迁移隔离业务库、构建政策索引并创建可观察 Web App。"""

        self.root = root
        self.business_database = root / "business.sqlite"
        self.checkpoint_database = root / "checkpoints.sqlite"
        self.policy_database = root / "policy.sqlite"
        self.clock = _EvalClock()
        self.secrets: list[str] = [PASSWORD, "eval-private-api-key"]
        upgrade_business_database(self.business_database)
        build_policy_index(POLICY_SOURCE, self.policy_database)
        self.settings = WebSettings(
            business_db_path=self.business_database,
            checkpoint_db_path=self.checkpoint_database,
            policy_source_path=POLICY_SOURCE,
            policy_index_db_path=self.policy_database,
            frontend_dist_path=root / "dist",
            allowed_origins=(ORIGIN,),
            llm_daily_call_limit=llm_limit,
        )
        self.interpreter = _EvalInterpreter()
        self.factory = _EvalInterpreterFactory(self.interpreter)
        self._open_app()

    def _open_app(self) -> None:
        """从现有磁盘文件创建新 Engine、Repository 和 App 实例。"""

        self.engine = create_business_engine(self.business_database)
        self.repository = SqliteBusinessRepository(
            self.engine,
            now_provider=self.clock,
        )
        self.services = WebServices(
            settings=self.settings,
            repository=self.repository,
            policy_repository=SqlitePolicyRepository(
                self.policy_database,
                source_root=POLICY_SOURCE,
            ),
            registered_interpreter_factory=self.factory,
            model_configured=True,
        )
        self.client = TestClient(
            create_app(services=self.services, mount_spa=False),
            base_url=ORIGIN,
            raise_server_exceptions=False,
        )

    def close(self) -> None:
        """关闭当前浏览器客户端和业务数据库 Engine。"""

        self.client.close()
        self.engine.dispose()

    def restart(self, session_token: str) -> None:
        """销毁当前应用并以同一持久文件和 Cookie 模拟服务重启。"""

        self.close()
        self._open_app()
        self.client.cookies.set(self.settings.cookie_name, session_token)

    def session(self, client: TestClient | None = None) -> dict[str, object]:
        """取得指定浏览器的当前公开 Session。"""

        response = (client or self.client).get("/api/session")
        assert response.status_code == 200
        return response.json()

    def headers(self, csrf: str) -> dict[str, str]:
        """构造通过同源和 CSRF 检查的写请求 Header。"""

        return {"Origin": ORIGIN, "X-CSRF-Token": csrf}

    def register_login(
        self,
        username: str,
        client: TestClient | None = None,
    ) -> dict[str, object]:
        """通过新邀请码注册并登录指定浏览器账号。"""

        selected = client or self.client
        session = self.session(selected)
        csrf = str(session["csrf_token"])
        invitation = self.repository.create_invitation()
        self.secrets.append(invitation.code)
        registered = selected.post(
            "/api/auth/register",
            headers=self.headers(csrf),
            json={
                "username": username,
                "password": PASSWORD,
                "invitation_code": invitation.code,
            },
        )
        assert registered.status_code == 201
        logged_in = selected.post(
            "/api/auth/login",
            headers=self.headers(csrf),
            json={"username": username, "password": PASSWORD},
        )
        assert logged_in.status_code == 200
        return logged_in.json()

    def conversation(self, csrf: str, client: TestClient | None = None) -> str:
        """为指定浏览器当前身份创建 conversation。"""

        response = (client or self.client).post(
            "/api/conversations",
            headers=self.headers(csrf),
        )
        assert response.status_code == 201
        return str(response.json()["thread_id"])

    def create_order(
        self,
        csrf: str,
        *,
        order_id: str = "ORD-PRIVATE",
        client: TestClient | None = None,
    ) -> None:
        """在指定注册账号工作区创建带物流的演示订单。"""

        response = (client or self.client).post(
            "/api/orders",
            headers=self.headers(csrf),
            json={
                "order_id": order_id,
                "status": "shipped",
                "shipment": {
                    "status": "in_transit",
                    "last_event": "到达北京分拨中心",
                },
            },
        )
        assert response.status_code == 201

    def chat(
        self,
        csrf: str,
        thread_id: str,
        message: str,
        client: TestClient | None = None,
    ):
        """向指定浏览器已授权 thread 提交一条消息。"""

        return (client or self.client).post(
            "/api/chat/messages",
            headers=self.headers(csrf),
            json={"thread_id": thread_id, "message": message},
        )

    def new_client(self) -> TestClient:
        """创建共享服务但 Cookie 隔离的第二个浏览器。"""

        return TestClient(
            create_app(services=self.services, mount_spa=False),
            base_url=ORIGIN,
            raise_server_exceptions=False,
        )

    def assert_no_plaintext_secrets(self) -> None:
        """扫描持久文件，确保密码、邀请码和 API Key 未以明文保存。"""

        for path in self.root.glob("*.sqlite*"):
            content = path.read_bytes()
            for secret in self.secrets:
                assert secret.encode() not in content


def _run_guest_scenario(runtime: _ScenarioRuntime, scenario_id: str) -> None:
    """执行四个游客只读、政策和零模型调用场景。"""

    session = runtime.session()
    csrf = str(session["csrf_token"])
    if scenario_id == "guest-write-denied":
        response = runtime.client.post(
            "/api/orders",
            headers=runtime.headers(csrf),
            json={"order_id": "ORD-GUEST", "status": "processing"},
        )
        assert response.status_code == 401
        assert runtime.repository.count_users() == 0
        return
    thread_id = runtime.conversation(csrf)
    message = {
        "guest-order": "查询 ORD-001",
        "guest-logistics": "帮我看看 ORD-001 到哪里了",
        "guest-policy": "普通商品退货期限是几天",
    }[scenario_id]
    response = runtime.chat(csrf, thread_id, message)
    assert response.status_code == 200
    if scenario_id == "guest-policy":
        assert response.json()["citations"]
    else:
        assert "ORD-001" in response.json()["assistant_message"]
    assert runtime.factory.calls == 0


def _run_invitation_scenario(runtime: _ScenarioRuntime, scenario_id: str) -> None:
    """执行邀请可用性、重复消费、登录退出和过期 Session 场景。"""

    if scenario_id == "invite-valid":
        session = runtime.session()
        invitation = runtime.repository.create_invitation()
        runtime.secrets.append(invitation.code)
        response = runtime.client.post(
            "/api/auth/register",
            headers=runtime.headers(str(session["csrf_token"])),
            json={
                "username": "user.valid",
                "password": PASSWORD,
                "invitation_code": invitation.code,
            },
        )
        assert response.status_code == 201
        assert runtime.repository.count_users() == 1
        return
    if scenario_id == "invite-unavailable":
        session = runtime.session()
        invitation = runtime.repository.create_invitation(expires_in_hours=1)
        runtime.repository.revoke_invitation(invitation.id)
        runtime.secrets.append(invitation.code)
        response = runtime.client.post(
            "/api/auth/register",
            headers=runtime.headers(str(session["csrf_token"])),
            json={
                "username": "user.denied",
                "password": PASSWORD,
                "invitation_code": invitation.code,
            },
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "invitation_unavailable"
        return
    if scenario_id == "invite-repeat":
        session = runtime.session()
        invitation = runtime.repository.create_invitation(max_uses=1)
        runtime.secrets.append(invitation.code)
        headers = runtime.headers(str(session["csrf_token"]))
        first = runtime.client.post(
            "/api/auth/register",
            headers=headers,
            json={
                "username": "user.first",
                "password": PASSWORD,
                "invitation_code": invitation.code,
            },
        )
        second = runtime.client.post(
            "/api/auth/register",
            headers=headers,
            json={
                "username": "user.second",
                "password": PASSWORD,
                "invitation_code": invitation.code,
            },
        )
        assert first.status_code == 201
        assert second.status_code == 400
        assert runtime.repository.count_users() == 1
        return
    logged_in = runtime.register_login("user.auth")
    if scenario_id == "login-logout":
        response = runtime.client.post(
            "/api/auth/logout",
            headers=runtime.headers(str(logged_in["csrf_token"])),
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "guest"
        assert runtime.client.get("/api/orders").status_code == 401
        return
    runtime.clock.value += timedelta(hours=25)
    expired = runtime.client.get("/api/session")
    assert expired.status_code == 200
    assert expired.json()["mode"] == "guest"


def _run_private_data_scenario(runtime: _ScenarioRuntime, scenario_id: str) -> None:
    """执行私有 CRUD、同号隔离、Schema 和跨账号权限场景。"""

    session = runtime.register_login("user.a")
    csrf = str(session["csrf_token"])
    headers = runtime.headers(csrf)
    if scenario_id == "private-invalid-schema":
        invalid = runtime.client.post(
            "/api/orders",
            headers=headers,
            json={"order_id": "bad", "status": "unknown"},
        )
        assert invalid.status_code == 422
        assert runtime.client.get("/api/orders").json()["orders"] == []
        return
    runtime.create_order(csrf, order_id="ORD-SAME")
    if scenario_id == "private-create-list":
        listed = runtime.client.get("/api/orders")
        assert listed.status_code == 200
        assert listed.json()["orders"][0]["order_id"] == "ORD-SAME"
        assert "workspace_id" not in listed.text
        return
    if scenario_id == "private-update-delete":
        updated = runtime.client.patch(
            "/api/orders/ORD-SAME",
            headers=headers,
            json={
                "status": "delivered",
                "shipment": {
                    "status": "delivered",
                    "last_event": "本人已签收",
                },
            },
        )
        deleted = runtime.client.delete("/api/orders/ORD-SAME", headers=headers)
        assert updated.json()["shipment"]["last_event"] == "本人已签收"
        assert deleted.json() == {"deleted": True}
        assert runtime.client.get("/api/orders").json()["orders"] == []
        return

    client_b = runtime.new_client()
    try:
        session_b = runtime.register_login("user.b", client_b)
        csrf_b = str(session_b["csrf_token"])
        if scenario_id == "private-same-order":
            runtime.create_order(csrf_b, order_id="ORD-SAME", client=client_b)
            order_b = client_b.get("/api/orders").json()["orders"][0]
            order_a = runtime.client.get("/api/orders").json()["orders"][0]
            assert order_b["status"] == "shipped"
            assert order_a["order_id"] == "ORD-SAME"
            return
        denied = client_b.patch(
            "/api/orders/ORD-SAME",
            headers=runtime.headers(csrf_b),
            json={"status": "cancelled"},
        )
        forged = client_b.post(
            "/api/orders",
            headers=runtime.headers(csrf_b),
            json={
                "order_id": "ORD-FORGED",
                "status": "processing",
                "workspace_id": "other",
            },
        )
        assert denied.status_code == 404
        assert forged.status_code == 422
    finally:
        client_b.close()


def _run_llm_scenario(runtime: _ScenarioRuntime, scenario_id: str) -> None:
    """执行私有查询、政策、客户端伪造和配额拒绝场景。"""

    session = runtime.register_login("user.llm")
    csrf = str(session["csrf_token"])
    runtime.create_order(csrf)
    thread_id = runtime.conversation(csrf)
    if scenario_id == "llm-forged-mode":
        response = runtime.client.post(
            "/api/chat/messages",
            headers=runtime.headers(csrf),
            json={
                "thread_id": thread_id,
                "message": "查询 ORD-PRIVATE",
                "interpreter": "fake",
                "workspace_id": "demo",
            },
        )
        assert response.status_code == 422
        assert runtime.factory.calls == 0
        return
    if scenario_id == "llm-policy":
        response = runtime.chat(csrf, thread_id, "普通商品退货期限是几天")
        assert response.status_code == 200
        assert response.json()["citations"]
        return
    first = runtime.chat(csrf, thread_id, "查询 ORD-PRIVATE")
    assert first.status_code == 200
    assert "北京分拨中心" in first.json()["assistant_message"]
    if scenario_id == "llm-quota":
        second = runtime.chat(csrf, thread_id, "再次查询 ORD-PRIVATE")
        assert second.status_code == 429
        assert second.json()["error_code"] == "llm_quota_exceeded"


def _run_recovery_scenario(runtime: _ScenarioRuntime, scenario_id: str) -> None:
    """执行跨应用恢复和身份不匹配时授权先行场景。"""

    session = runtime.register_login("user.owner")
    csrf = str(session["csrf_token"])
    runtime.create_order(csrf)
    thread_id = runtime.conversation(csrf)
    first = runtime.chat(csrf, thread_id, "帮我查一下物流")
    assert first.json()["public_status"] == "awaiting_order_id"
    if scenario_id == "recovery-cross-instance":
        token = runtime.client.cookies.get(runtime.settings.cookie_name)
        assert token is not None
        runtime.restart(token)
        restored = runtime.session()
        response = runtime.chat(
            str(restored["csrf_token"]),
            thread_id,
            "ORD-PRIVATE",
        )
        assert response.status_code == 200
        assert response.json()["public_status"] == "completed"
        return
    client_b = runtime.new_client()
    try:
        session_b = runtime.register_login("user.other", client_b)
        denied = runtime.chat(
            str(session_b["csrf_token"]),
            thread_id,
            "ORD-PRIVATE",
            client_b,
        )
        assert denied.status_code == 404
    finally:
        client_b.close()


def _execute_scenario(scenario: V03EvalScenario, root: Path) -> V03EvalScenarioResult:
    """在独立磁盘数据库中执行一个完整 HTTP 场景并扫描明文凭据。"""

    runtime = _ScenarioRuntime(
        root,
        llm_limit=1 if scenario.scenario_id == "llm-quota" else 20,
    )
    try:
        if scenario.category == "guest":
            _run_guest_scenario(runtime, scenario.scenario_id)
        elif scenario.category == "invitation_auth":
            _run_invitation_scenario(runtime, scenario.scenario_id)
        elif scenario.category == "private_data":
            _run_private_data_scenario(runtime, scenario.scenario_id)
        elif scenario.category == "registered_llm":
            _run_llm_scenario(runtime, scenario.scenario_id)
        else:
            _run_recovery_scenario(runtime, scenario.scenario_id)
        runtime.assert_no_plaintext_secrets()
        return V03EvalScenarioResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            passed=True,
        )
    except Exception as error:
        violations = (
            (scenario.security_metric,) if scenario.security_metric is not None else ()
        )
        return V03EvalScenarioResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            passed=False,
            security_violations=violations,
            error_type=type(error).__name__,
        )
    finally:
        runtime.close()


def run_v03_eval_suite() -> V03EvalReport:
    """运行固定 20 个 v0.3 场景并按零安全违规门槛汇总。"""

    with TemporaryDirectory(prefix="commerce-resolve-v03-eval-") as directory:
        root = Path(directory)
        results = tuple(
            _execute_scenario(scenario, root / scenario.scenario_id)
            for scenario in SCENARIOS
        )
    violation_counts = Counter(
        violation for result in results for violation in result.security_violations
    )
    passed_scenarios = sum(result.passed for result in results)
    passed = passed_scenarios == len(SCENARIOS) and not violation_counts
    return V03EvalReport(
        suite="v0.3",
        total_scenarios=len(SCENARIOS),
        passed_scenarios=passed_scenarios,
        guest_llm_calls=violation_counts["guest_llm_call"],
        unauthorized_business_writes=violation_counts["unauthorized_write"],
        forgery_successes=violation_counts["forgery_success"],
        invitation_overconsumption=violation_counts["invitation_overconsumption"],
        cross_user_leaks=violation_counts["cross_user_leak"],
        credential_leaks=violation_counts["credential_leak"],
        passed=passed,
        category_counts=dict(Counter(item.category for item in SCENARIOS)),
        results=results,
    )
