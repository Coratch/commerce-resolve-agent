"""提供 v0.3 Web 集成测试共用的迁移数据库与 Fake 模型装配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from commerce_resolve.adapters.fake import FakeQueryInterpreter
from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.business_models import MockPaymentInput, OrderCreate, OrderUpdate
from commerce_resolve.gateways import QueryInterpreter
from commerce_resolve.l2_memory import setup_memory_store
from commerce_resolve.models import Interpretation, InterpretationContext
from commerce_resolve.web.app import create_app
from commerce_resolve.web.dependencies import WebServices
from commerce_resolve.web.settings import WebSettings

ORIGIN = "http://testserver"
PASSWORD = "correct horse battery"


class SpyRegisteredInterpreter:
    """模拟注册用户 LLM 的宽订单号结构化输出并记录调用。"""

    def __init__(self) -> None:
        """初始化调用记录和政策查询委托。"""

        self.calls: list[str] = []
        self._policy_interpreter = FakeQueryInterpreter()

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """识别测试用自定义订单号，其余政策语义使用确定性规则。"""

        self.calls.append(text)
        return self._policy_interpreter.interpret(text, context)


class InterpreterFactory:
    """重复返回同一个可观察 Fake Interpreter 供注册路径测试。"""

    def __init__(self, interpreter: QueryInterpreter) -> None:
        """保存待注入的解释器实例。"""

        self.interpreter = interpreter
        self.calls = 0

    def __call__(self) -> QueryInterpreter:
        """记录依赖装配次数并返回解释器。"""

        self.calls += 1
        return self.interpreter


@dataclass
class WebHarness:
    """集中保存测试客户端、业务仓库、配置与 Spy Interpreter。"""

    client: TestClient
    repository: SqliteBusinessRepository
    services: WebServices
    interpreter: SpyRegisteredInterpreter
    factory: InterpreterFactory

    def session(self) -> dict[str, object]:
        """取得或恢复浏览器 Session，并返回公开响应。"""

        response = self.client.get("/api/session")
        assert response.status_code == 200
        return response.json()

    def mutation_headers(self, csrf_token: str | None) -> dict[str, str]:
        """构造同源写请求 Header，并仅在存在登录 Token 时携带 CSRF。"""

        headers = {"Origin": ORIGIN}
        if csrf_token:
            headers["X-CSRF-Token"] = csrf_token
        return headers

    def current_username(self) -> str:
        """从当前 Cookie 解析账号名，且不轮换同步 CSRF Token。"""

        token = self.client.cookies.get(self.services.settings.cookie_name)
        assert token is not None
        identity = self.repository.resolve_session(token)
        assert identity is not None and identity.username is not None
        return identity.username

    def register_and_login(self, username: str) -> dict[str, object]:
        """通过一次性邀请码注册并登录，返回轮换后的会话响应。"""

        session = self.session()
        csrf = session.get("csrf_token")
        invite = self.repository.create_invitation()
        registered = self.client.post(
            "/api/auth/register",
            headers=self.mutation_headers(csrf),
            json={
                "username": username,
                "password": PASSWORD,
                "invitation_code": invite.code,
            },
        )
        assert registered.status_code == 201
        logged_in = self.client.post(
            "/api/auth/login",
            headers=self.mutation_headers(csrf),
            json={"username": username, "password": PASSWORD},
        )
        assert logged_in.status_code == 200
        return logged_in.json()

    def first_order_id(self) -> str:
        """返回当前注册账号工作区中的第一笔演示订单号。"""

        response = self.client.get("/api/support/orders")
        assert response.status_code == 200
        orders = response.json()["orders"]
        assert orders
        return str(orders[0]["order_id"])

    def create_order_conversation(
        self,
        headers: dict[str, str],
        order_id: str | None = None,
    ) -> dict[str, object]:
        """创建或恢复当前账号指定订单的活动会话。"""

        target_order_id = order_id or self.first_order_id()
        response = self.client.post(
            "/api/conversations",
            headers=headers,
            json={"related_order_id": target_order_id},
        )
        assert response.status_code in {200, 201}
        return response.json()

    def latest_public_response(self, thread_id: str) -> dict[str, object]:
        """从服务端公开历史还原最近一条助手响应，供异步 Run 验收。"""

        response = self.client.get(f"/api/conversations/{thread_id}/messages")
        assert response.status_code == 200
        assistant = next(
            item
            for item in reversed(response.json()["messages"])
            if item["role"] == "assistant"
        )
        return {
            "thread_id": thread_id,
            "assistant_message": assistant["content"],
            **assistant["payload"],
        }

    def seed_order(self, username: str, payload: dict[str, object]):
        """模拟后台维护者向指定客户的权威工作区写入 Mock 订单。"""

        target = next(
            item
            for item in SqliteAdminRepository(self.repository.engine).list_customers()
            if item.username == username
        )
        return self.repository.create_order(
            user_id=target.user_id,
            workspace_id=target.workspace_id,
            data=OrderCreate.model_validate(payload),
        )

    def update_seeded_order(
        self,
        username: str,
        order_id: str,
        payload: dict[str, object],
    ):
        """模拟后台维护者修改指定客户的既有 Mock 订单。"""

        target = next(
            item
            for item in SqliteAdminRepository(self.repository.engine).list_customers()
            if item.username == username
        )
        return self.repository.update_order(
            user_id=target.user_id,
            workspace_id=target.workspace_id,
            order_id=order_id,
            data=OrderUpdate.model_validate(payload),
        )

    def seed_payment(
        self,
        username: str,
        order_id: str,
        payload: dict[str, object],
    ):
        """模拟后台维护者向指定客户订单写入退款前 Mock 支付。"""

        target = next(
            item
            for item in SqliteAdminRepository(self.repository.engine).list_customers()
            if item.username == username
        )
        return self.services.require_refund_repository().upsert_payment(
            user_id=target.user_id,
            workspace_id=target.workspace_id,
            order_id=order_id,
            data=MockPaymentInput.model_validate(payload),
        )


@pytest.fixture
def web_harness(tmp_path: Path) -> WebHarness:
    """创建完成迁移、政策索引和 Fake 模型注入的同源 Web 应用。"""

    business_database = tmp_path / "business.sqlite"
    checkpoint_database = tmp_path / "checkpoints.sqlite"
    memory_database = tmp_path / "memory.sqlite"
    policy_database = tmp_path / "policy.sqlite"
    source = Path("data/policies")
    upgrade_business_database(business_database)
    build_policy_index(source, policy_database)
    setup_memory_store(memory_database)
    engine = create_business_engine(business_database)
    repository = SqliteBusinessRepository(engine)
    interpreter = SpyRegisteredInterpreter()
    factory = InterpreterFactory(interpreter)
    settings = WebSettings(
        business_db_path=business_database,
        checkpoint_db_path=checkpoint_database,
        policy_source_path=source,
        policy_index_db_path=policy_database,
        memory_db_path=memory_database,
        eval_run_root=tmp_path / "eval-runs",
        eval_baseline_path=tmp_path / "offline-baseline.json",
        frontend_dist_path=tmp_path / "dist",
        allowed_origins=(ORIGIN,),
    )
    services = WebServices(
        settings=settings,
        repository=repository,
        policy_repository=SqlitePolicyRepository(
            policy_database,
            source_root=source,
        ),
        registered_interpreter_factory=factory,
        model_configured=True,
    )
    client = TestClient(
        create_app(services=services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    yield WebHarness(client, repository, services, interpreter, factory)
    client.close()
    engine.dispose()
