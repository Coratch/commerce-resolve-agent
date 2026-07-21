"""为 Playwright 提供使用临时数据库和离线模型的真实 HTTP 服务。"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from commerce_resolve.adapters.fake import FakeQueryInterpreter
from commerce_resolve.adapters.fake_l2_agent import ScriptedL2Agent
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
from commerce_resolve.l2_models import (
    AnswerDecision,
    GetOrderCall,
    GetShipmentCall,
    ToolCallDecision,
)
from commerce_resolve.models import (
    Interpretation,
    InterpretationContext,
    RefundReason,
)
from commerce_resolve.web.app import create_app
from commerce_resolve.web.dependencies import WebServices
from commerce_resolve.web.settings import WebSettings
from commerce_resolve.web.spa import register_spa_routes

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="commerce-resolve-e2e-"))


class E2EInterpreter:
    """为浏览器测试提供无网络的注册用户结构化意图。"""

    def __init__(self) -> None:
        """初始化政策语义委托。"""

        self._policy = FakeQueryInterpreter()

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """识别自定义订单号，并委托既有规则解释政策问题。"""

        match = re.search(r"\bORD-[A-Z0-9-]{3,32}\b", text, re.IGNORECASE)
        if any(keyword in text for keyword in ("二线客服", "升级处理", "复杂售后")):
            return Interpretation(
                intent="l2_support_request",
                order_id=match.group(0).upper() if match is not None else None,
                l2_issue_summary=text[:500],
            )
        if match is not None:
            if "退款" in text:
                return Interpretation(
                    intent="refund_request",
                    order_id=match.group(0).upper(),
                    refund_reason=RefundReason(code="quality_issue"),
                )
            return Interpretation(
                intent="order_inquiry",
                order_id=match.group(0).upper(),
            )
        return self._policy.interpret(text, context)


class E2EInterpreterFactory:
    """为每轮注册对话返回同一个离线解释器。"""

    def __init__(self) -> None:
        """创建离线解释器实例。"""

        self._interpreter = E2EInterpreter()

    def __call__(self) -> QueryInterpreter:
        """返回无网络解释器。"""

        return self._interpreter


def build_e2e_app():
    """创建包含生产 SPA 和测试专用邀请签发端点的临时应用。"""

    business_database = RUNTIME_ROOT / "business.sqlite"
    policy_database = RUNTIME_ROOT / "policy.sqlite"
    checkpoint_database = RUNTIME_ROOT / "checkpoints.sqlite"
    memory_database = RUNTIME_ROOT / "memory.sqlite"
    upgrade_business_database(business_database)
    build_policy_index(PROJECT_ROOT / "data" / "policies", policy_database)
    setup_memory_store(memory_database)
    engine = create_business_engine(business_database)
    repository = SqliteBusinessRepository(engine)
    settings = WebSettings(
        business_db_path=business_database,
        checkpoint_db_path=checkpoint_database,
        policy_source_path=PROJECT_ROOT / "data" / "policies",
        policy_index_db_path=policy_database,
        memory_db_path=memory_database,
        frontend_dist_path=PROJECT_ROOT / "frontend" / "dist",
        allowed_origins=("http://127.0.0.1:8000",),
    )
    order_id = "ORD-E2E-001"
    l2_agent = ScriptedL2Agent(
        (
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id=order_id),
            ),
            ToolCallDecision(
                kind="tool_call",
                call=GetShipmentCall(tool="get_shipment", order_id=order_id),
            ),
            AnswerDecision(
                kind="answer",
                answer="已核对订单和物流，当前退款记录与运输状态均已纳入处理结论。",
                evidence_ids=(
                    f"order:{order_id}:processing",
                    f"shipment:{order_id}:preparing",
                ),
            ),
        )
    )
    services = WebServices(
        settings=settings,
        repository=repository,
        policy_repository=SqlitePolicyRepository(
            policy_database,
            source_root=settings.policy_source_path,
        ),
        registered_interpreter_factory=E2EInterpreterFactory(),
        l2_agent_factory=lambda: l2_agent,
        model_configured=True,
        engine=engine,
    )
    application = create_app(services=services, mount_spa=False)

    @application.post("/api/test/invitation", include_in_schema=False)
    def issue_test_invitation() -> dict[str, str]:
        """只在隔离 E2E 进程中签发一次性邀请码。"""

        invitation = repository.create_invitation(expires_in_hours=1)
        return {"code": invitation.code}

    register_spa_routes(application, settings.frontend_dist_path)
    return application


app = build_e2e_app()
