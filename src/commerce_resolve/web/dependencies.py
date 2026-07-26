"""装配 Web 请求使用的可信身份、依赖、限流与 thread 互斥。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Request, Response
from sqlalchemy import Engine

from commerce_resolve.access import AccessPrincipal, LlmAccessPolicy
from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.l2_freshness import GatewayL2FreshnessReader
from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessGateway,
    SqliteBusinessRepository,
    assert_business_schema_current,
    create_business_engine,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository
from commerce_resolve.adapters.sqlite_policy import SqlitePolicyRepository
from commerce_resolve.adapters.sqlite_refunds import (
    SqliteRefundGateway,
    SqliteRefundRepository,
)
from commerce_resolve.adapters.sqlite_workspaces import SqliteWorkspaceRepository
from commerce_resolve.business_models import SessionBundle, SessionIdentity
from commerce_resolve.gateways import Dependencies, QueryInterpreter
from commerce_resolve.l2_gateways import L2AgentModel, L2Dependencies
from commerce_resolve.l2_memory import assert_memory_store_ready
from commerce_resolve.l2_tools import L2ToolRegistry
from commerce_resolve.models import (
    Interpretation,
    InterpretationContext,
)
from commerce_resolve.service_center import GuestSupportCatalog

from .errors import api_error
from .schemas import SessionCapabilities, SessionResponse
from .settings import WebSettings


@dataclass(frozen=True)
class RequestAccess:
    """保存一次请求中已验证的 Session Token、身份和可信 Principal。"""

    session_token: str
    identity: SessionIdentity
    principal: AccessPrincipal


class InMemoryRateLimiter:
    """提供单实例内、固定窗口的最小请求频率限制。"""

    def __init__(self, now_provider: Callable[[], float] = time.monotonic) -> None:
        """保存可替换单调时钟和受锁保护的窗口计数。"""

        self._now = now_provider
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        """在固定窗口未超过上限时记录一次请求并返回允许。"""

        now = self._now()
        with self._lock:
            started_at, count = self._buckets.get(key, (now, 0))
            if now - started_at >= window_seconds:
                started_at, count = now, 0
            if count >= limit:
                return False
            self._buckets[key] = (started_at, count + 1)
            return True


class ThreadLockRegistry:
    """拒绝同一进程中对相同 conversation 的并发 Graph 执行。"""

    def __init__(self) -> None:
        """初始化受锁保护的活动 thread 集合。"""

        self._active: set[str] = set()
        self._lock = threading.Lock()

    @contextmanager
    def acquire(self, thread_id: str) -> Iterator[bool]:
        """非阻塞占用 thread，并在请求结束时可靠释放。"""

        with self._lock:
            acquired = thread_id not in self._active
            if acquired:
                self._active.add(thread_id)
        try:
            yield acquired
        finally:
            if acquired:
                with self._lock:
                    self._active.discard(thread_id)


class _FailIfCalledInterpreter:
    """在审批恢复误走意图识别时立即失败，防止额外模型调用。"""

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """拒绝恢复路径中的任何 Interpreter 调用。"""

        del text, context
        raise RuntimeError("审批恢复不应调用 Interpreter")


@dataclass
class WebServices:
    """集中保存应用级配置、持久仓库和可替换运行时依赖。"""

    settings: WebSettings
    repository: SqliteBusinessRepository
    policy_repository: SqlitePolicyRepository
    registered_interpreter_factory: Callable[[], QueryInterpreter]
    model_configured: bool
    conversation_repository: SqliteConversationRepository | None = None
    refund_repository: SqliteRefundRepository | None = None
    l2_repository: SqliteL2CaseRepository | None = None
    admin_repository: SqliteAdminRepository | None = None
    workspace_repository: SqliteWorkspaceRepository | None = None
    l2_agent_factory: Callable[[], L2AgentModel] | None = None
    engine: Engine | None = None
    llm_access_policy: LlmAccessPolicy = field(default_factory=LlmAccessPolicy)
    rate_limiter: InMemoryRateLimiter = field(default_factory=InMemoryRateLimiter)
    thread_locks: ThreadLockRegistry = field(default_factory=ThreadLockRegistry)
    workspace_locks: ThreadLockRegistry = field(default_factory=ThreadLockRegistry)
    guest_catalog: GuestSupportCatalog = field(default_factory=GuestSupportCatalog)

    def __post_init__(self) -> None:
        """默认让公开会话、退款与 L2 Repository 复用业务 Engine。"""

        if self.conversation_repository is None:
            self.conversation_repository = SqliteConversationRepository(
                self.repository.engine
            )
        if self.refund_repository is None:
            self.refund_repository = SqliteRefundRepository(self.repository.engine)
        if self.l2_repository is None:
            self.l2_repository = SqliteL2CaseRepository(self.repository.engine)
        if self.admin_repository is None:
            self.admin_repository = SqliteAdminRepository(self.repository.engine)
        if self.workspace_repository is None:
            self.workspace_repository = SqliteWorkspaceRepository(
                self.repository.engine
            )
        if self.l2_agent_factory is None:
            self.l2_agent_factory = _default_scripted_l2_agent

    def require_refund_repository(self) -> SqliteRefundRepository:
        """返回已装配的退款仓库；缺失表示应用装配错误。"""

        if self.refund_repository is None:
            raise RuntimeError("退款仓库未装配")
        return self.refund_repository

    def require_conversation_repository(self) -> SqliteConversationRepository:
        """返回公开会话仓库；缺失表示应用装配错误。"""

        if self.conversation_repository is None:
            raise RuntimeError("公开会话仓库未装配")
        return self.conversation_repository

    def require_l2_repository(self) -> SqliteL2CaseRepository:
        """返回已装配 L2 Repository；缺失表示应用装配错误。"""

        if self.l2_repository is None:
            raise RuntimeError("L2 Case Repository 未装配")
        return self.l2_repository

    def require_admin_repository(self) -> SqliteAdminRepository:
        """返回运营控制台 Repository；缺失表示应用装配错误。"""

        if self.admin_repository is None:
            raise RuntimeError("Admin Repository 未装配")
        return self.admin_repository

    def require_workspace_repository(self) -> SqliteWorkspaceRepository:
        """返回演示工作区 Repository；缺失表示应用装配错误。"""

        if self.workspace_repository is None:
            raise RuntimeError("Workspace Repository 未装配")
        return self.workspace_repository

    def require_l2_agent_factory(self) -> Callable[[], L2AgentModel]:
        """返回请求级 L2 Model 工厂，避免把客户端或连接写入 State。"""

        if self.l2_agent_factory is None:
            raise RuntimeError("L2 Model Factory 未装配")
        return self.l2_agent_factory

    def _l2_dependencies(self) -> L2Dependencies:
        """为注册路径构造共享计量、工具白名单和请求级模型适配器。"""

        return L2Dependencies(
            agent_model=self.require_l2_agent_factory()(),
            case_repository=self.require_l2_repository(),
            tool_registry=L2ToolRegistry(),
            conversation_reader=self.require_conversation_repository(),
            freshness_reader=GatewayL2FreshnessReader(
                order_gateway=SqliteBusinessGateway(self.repository),
                logistics_gateway=SqliteBusinessGateway(self.repository),
                policy_repository=self.policy_repository,
                refund_gateway=SqliteRefundGateway(self.require_refund_repository()),
            ),
            daily_call_limit=self.settings.llm_daily_call_limit,
        )

    def guest_dependencies(self, principal: AccessPrincipal) -> Dependencies:
        """为游客装配只含共享 demo 数据和 Fake Interpreter 的依赖。"""

        order = self.guest_catalog.order_view(principal.actor_id)
        shipment = self.guest_catalog.shipment_view()
        return Dependencies(
            interpreter=FakeQueryInterpreter(),
            order_gateway=FakeOrderGateway(
                {(principal.actor_id, order.order_id): order}
            ),
            logistics_gateway=FakeLogisticsGateway({order.order_id: shipment}),
            policy_repository=self.policy_repository,
        )

    def registered_dependencies(self) -> Dependencies:
        """为注册用户装配真实 Interpreter 与按工作区隔离的业务 Gateway。"""

        gateway = SqliteBusinessGateway(self.repository)
        refund_repository = self.require_refund_repository()
        return Dependencies(
            interpreter=self.registered_interpreter_factory(),
            order_gateway=gateway,
            logistics_gateway=gateway,
            policy_repository=self.policy_repository,
            refund_gateway=SqliteRefundGateway(refund_repository),
            l2=self._l2_dependencies(),
        )

    def l2_resume_dependencies(self) -> Dependencies:
        """为 L2 恢复装配哨兵 Interpreter、业务工具和 L2 Harness。"""

        gateway = SqliteBusinessGateway(self.repository)
        return Dependencies(
            interpreter=_FailIfCalledInterpreter(),
            order_gateway=gateway,
            logistics_gateway=gateway,
            policy_repository=self.policy_repository,
            refund_gateway=SqliteRefundGateway(self.require_refund_repository()),
            l2=self._l2_dependencies(),
        )

    def refund_resume_dependencies(self) -> Dependencies:
        """为审批恢复装配不会调用模型的业务依赖。"""

        return self.l2_resume_dependencies()

    def dispose(self) -> None:
        """关闭由默认应用工厂创建的 SQLAlchemy Engine。"""

        if self.engine is not None:
            self.engine.dispose()


def _default_registered_interpreter() -> QueryInterpreter:
    """延迟创建 OpenAI-compatible Interpreter，避免游客路径加载客户端。"""

    from commerce_resolve.adapters.openai_interpreter import OpenAIQueryInterpreter

    return OpenAIQueryInterpreter.from_env()


def _default_l2_agent() -> L2AgentModel:
    """延迟创建真实 OpenAI-compatible L2 Model Adapter。"""

    from commerce_resolve.adapters.openai_l2_agent import OpenAIL2Agent

    return OpenAIL2Agent.from_env()


def _default_scripted_l2_agent() -> L2AgentModel:
    """为显式注入的测试 WebServices 提供不会访问网络的空脚本模型。"""

    from commerce_resolve.adapters.fake_l2_agent import ScriptedL2Agent

    return ScriptedL2Agent(())


def create_default_services(settings: WebSettings) -> WebServices:
    """从不可变配置创建生产 Web 服务，并拒绝未迁移业务库。"""

    engine = create_business_engine(settings.business_db_path)
    try:
        assert_business_schema_current(engine, settings.business_db_path)
        assert_memory_store_ready(settings.memory_db_path)
    except Exception:
        engine.dispose()
        raise
    return WebServices(
        settings=settings,
        repository=SqliteBusinessRepository(engine),
        policy_repository=SqlitePolicyRepository(
            settings.policy_index_db_path,
            source_root=settings.policy_source_path,
        ),
        registered_interpreter_factory=_default_registered_interpreter,
        l2_agent_factory=_default_l2_agent,
        model_configured=settings.model_configured(),
        engine=engine,
    )


def get_services(request: Request) -> WebServices:
    """从 FastAPI App State 取得应用工厂注入的服务集合。"""

    return request.app.state.services


def principal_from_identity(
    identity: SessionIdentity,
    services: WebServices,
) -> AccessPrincipal:
    """把持久 Session 身份转换为客户端无法构造的访问主体。"""

    actor_id = identity.user_id or identity.subject_id
    return AccessPrincipal(
        actor_id=actor_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        mode=identity.actor_type,
        llm_allowed=(
            identity.actor_type == "registered"
            and identity.user_id is not None
            and services.settings.llm_feature_enabled
            and services.model_configured
        ),
        role=identity.user_role,
    )


def resolve_request_access(request: Request) -> RequestAccess:
    """解析有效 Session Cookie；缺失、过期或撤销时统一要求重新建立会话。"""

    services = get_services(request)
    session_token = request.cookies.get(services.settings.cookie_name)
    if not session_token:
        raise api_error(401, "authentication_required")
    identity = services.repository.resolve_session(session_token)
    if identity is None:
        raise api_error(401, "authentication_required")
    return RequestAccess(
        session_token=session_token,
        identity=identity,
        principal=principal_from_identity(identity, services),
    )


def require_mutation_access(request: Request) -> RequestAccess:
    """依次验证 Origin、Session 和同步 CSRF Token。"""

    services = get_services(request)
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in services.settings.allowed_origins:
        raise api_error(403, "origin_not_allowed")
    access = resolve_request_access(request)
    csrf_token = request.headers.get("x-csrf-token", "")
    verified = services.repository.verify_csrf(
        access.session_token,
        csrf_token,
    )
    if verified is None:
        raise api_error(403, "csrf_failed")
    return access


def require_public_mutation_origin(request: Request) -> None:
    """验证登录和注册等无 Session 写请求来自允许的同源页面。"""

    services = get_services(request)
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in services.settings.allowed_origins:
        raise api_error(403, "origin_not_allowed")


def require_registered_access(request: Request, *, mutation: bool) -> RequestAccess:
    """验证当前请求属于注册用户，并按需执行写请求来源检查。"""

    access = (
        require_mutation_access(request)
        if mutation
        else resolve_request_access(request)
    )
    if access.principal.mode != "registered" or access.principal.user_id is None:
        raise api_error(401, "authentication_required")
    return access


def require_admin_access(request: Request, *, mutation: bool) -> RequestAccess:
    """验证当前请求属于数据库已授予管理员角色的注册账号。"""

    access = require_registered_access(request, mutation=mutation)
    if access.principal.role != "admin":
        raise api_error(403, "admin_access_required")
    return access


def set_session_cookie(
    response: Response,
    services: WebServices,
    session_token: str,
    *,
    expires_at: datetime,
) -> None:
    """使用固定安全属性写入不透明 Session Cookie。"""

    response.set_cookie(
        key=services.settings.cookie_name,
        value=session_token,
        expires=expires_at,
        httponly=True,
        secure=services.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def build_session_response(
    identity: SessionIdentity | SessionBundle,
    csrf_token: str,
    services: WebServices,
) -> SessionResponse:
    """构造不暴露内部用户、工作区和 Session Token 的公开会话响应。"""

    if identity.actor_type != "registered":
        raise ValueError("公开 Session 只允许投影注册身份")
    can_use_llm = services.settings.llm_feature_enabled and services.model_configured
    return SessionResponse(
        mode="registered",
        username=identity.username,
        role=identity.user_role,
        session_scope="account",
        csrf_token=csrf_token,
        expires_at=identity.expires_at,
        capabilities=SessionCapabilities(
            can_manage_orders=False,
            can_manage_refunds=True,
            can_use_llm=can_use_llm,
            can_access_admin=identity.user_role == "admin",
        ),
    )


def build_anonymous_session_response() -> SessionResponse:
    """构造不创建 Cookie、工作区或业务能力的匿名公开状态。"""

    return SessionResponse(
        mode="anonymous",
        session_scope="none",
        capabilities=SessionCapabilities(
            can_manage_orders=False,
            can_manage_refunds=False,
            can_use_llm=False,
            can_access_admin=False,
        ),
    )


def enforce_rate_limit(
    services: WebServices,
    key: str,
    *,
    limit: int,
) -> None:
    """在单实例窗口超限时返回稳定 429 错误。"""

    if not services.rate_limiter.allow(key, limit=limit):
        raise api_error(429, "rate_limited")
