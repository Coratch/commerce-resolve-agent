"""提供 v0.3 Web 集成测试共用的迁移数据库与 Fake 模型装配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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

        import re

        self.calls.append(text)
        match = re.search(r"\bORD-[A-Z0-9-]{3,32}\b", text, re.IGNORECASE)
        if any(
            keyword in text
            for keyword in ("二线客服", "高级客服", "升级处理", "复杂售后")
        ):
            return Interpretation(
                intent="l2_support_request",
                order_id=match.group(0).upper() if match is not None else None,
                l2_issue_summary=text[:500],
            )
        if "退款" in text or (context is not None and context.pending_refund_request):
            from commerce_resolve.models import RefundReason

            reason = (
                RefundReason(code="quality_issue")
                if "质量" in text
                else RefundReason(code="no_longer_needed")
                if "不想要" in text
                else None
            )
            return Interpretation(
                intent="refund_request",
                order_id=match.group(0).upper() if match is not None else None,
                refund_reason=reason,
            )
        if match is not None:
            return Interpretation(
                intent="order_inquiry",
                order_id=match.group(0).upper(),
            )
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

    def mutation_headers(self, csrf_token: str) -> dict[str, str]:
        """构造通过同源与同步 CSRF 校验的写请求 Header。"""

        return {"Origin": ORIGIN, "X-CSRF-Token": csrf_token}

    def register_and_login(self, username: str) -> dict[str, object]:
        """通过一次性邀请码注册并登录，返回轮换后的会话响应。"""

        session = self.session()
        csrf = str(session["csrf_token"])
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
