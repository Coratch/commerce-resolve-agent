"""实现 v0.3 SQLite 业务仓库、迁移和受控业务 Gateway。"""

import hmac
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, event, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.access import BusinessScope
from commerce_resolve.auth import (
    AuthDomainError,
    PasswordService,
    generate_secret_token,
    hash_secret,
    normalize_username,
)
from commerce_resolve.business_models import (
    ConversationRecord,
    InvitationIssued,
    LlmUsageRecord,
    OrderCreate,
    OrderRecord,
    OrderStatus,
    OrderUpdate,
    RegistrationResult,
    SessionBundle,
    SessionIdentity,
    ShipmentRecord,
    ShipmentStatus,
    UserAccount,
    UserStatus,
    WebActorType,
    Workspace,
)
from commerce_resolve.models import OrderView, ShipmentView, ToolResult

from .sqlalchemy_models import (
    ConversationRow,
    InvitationRow,
    LlmDailyUsageRow,
    MockPaymentRow,
    OrderRow,
    RefundActionRow,
    RefundAuditEventRow,
    ShipmentRow,
    UserRow,
    WebSessionRow,
    WorkspaceRow,
    utc_now,
)

DEMO_WORKSPACE_ID = "demo"
MIGRATIONS_ROOT = Path(__file__).resolve().parents[3] / "migrations"


class BusinessDataError(ValueError):
    """表示可安全映射给 Web 客户端的业务数据错误。"""

    def __init__(self, error_code: str) -> None:
        """保存稳定错误码并隐藏数据库实现细节。"""

        super().__init__(error_code)
        self.error_code = error_code


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sqlite_url(database: Path) -> str:
    """生成使用绝对路径的 SQLite SQLAlchemy URL。"""

    return f"sqlite+pysqlite:///{database.resolve()}"


def create_business_engine(database: str | Path) -> Engine:
    """创建启用外键、WAL 和有限忙等待的 SQLite Engine。"""

    from sqlalchemy import create_engine

    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        _sqlite_url(path),
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        """为每个底层 SQLite 连接启用一致的安全与并发配置。"""

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def _alembic_config(database: str | Path) -> Config:
    """构造不依赖全局 alembic.ini 的项目迁移配置。"""

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_ROOT))
    config.set_main_option("sqlalchemy.url", _sqlite_url(Path(database)))
    return config


def upgrade_business_database(database: str | Path) -> None:
    """把业务数据库显式升级到当前 Alembic head。"""

    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(path), "head")


def assert_business_schema_current(engine: Engine, database: str | Path) -> None:
    """验证业务数据库位于 Alembic head，否则拒绝启动服务。"""

    config = _alembic_config(database)
    expected = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != expected:
        raise RuntimeError("业务数据库 Schema 未升级，请先执行 db upgrade")


class SqliteBusinessRepository:
    """提供账号、Session、会话、订单和配额的事务化持久化。"""

    def __init__(
        self,
        engine: Engine,
        *,
        password_service: PasswordService | None = None,
        now_provider: Callable[[], datetime] = utc_now,
    ) -> None:
        """保存 Engine、密码服务和可替换时钟以支持确定性测试。"""

        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._passwords = password_service or PasswordService()
        self._now = now_provider

    def _now_utc(self) -> datetime:
        """读取并规范化当前 UTC 时间。"""

        return _as_utc(self._now())

    def create_invitation(
        self,
        *,
        expires_in_hours: int = 168,
        max_uses: int = 1,
    ) -> InvitationIssued:
        """创建有限有效期的邀请码，并仅返回一次可用明文。"""

        if not 1 <= expires_in_hours <= 24 * 30 or not 1 <= max_uses <= 100:
            raise AuthDomainError("invalid_invitation_options")
        code = generate_secret_token()
        now = self._now_utc()
        row = InvitationRow(
            id=str(uuid4()),
            code_hash=hash_secret(code),
            expires_at=now + timedelta(hours=expires_in_hours),
            max_uses=max_uses,
            used_count=0,
            created_at=now,
        )
        with self._sessions.begin() as session:
            session.add(row)
        return InvitationIssued(
            id=row.id,
            code=code,
            expires_at=_as_utc(row.expires_at),
            max_uses=row.max_uses,
        )

    def revoke_invitation(self, invitation_id: str) -> bool:
        """主动失效邀请码，重复失效保持幂等。"""

        with self._sessions.begin() as session:
            row = session.get(InvitationRow, invitation_id)
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = self._now_utc()
            return True

    def register(
        self,
        *,
        username: str,
        password: str,
        invitation_code: str,
    ) -> RegistrationResult:
        """原子消费邀请码并创建唯一账号与私有工作区。"""

        normalized = normalize_username(username)
        password_hash = self._passwords.hash(password)
        now = self._now_utc()
        user_id = str(uuid4())
        workspace_id = str(uuid4())
        try:
            with self._sessions.begin() as session:
                consumed = session.execute(
                    update(InvitationRow)
                    .where(
                        InvitationRow.code_hash == hash_secret(invitation_code),
                        InvitationRow.revoked_at.is_(None),
                        InvitationRow.expires_at > now,
                        InvitationRow.used_count < InvitationRow.max_uses,
                    )
                    .values(used_count=InvitationRow.used_count + 1)
                )
                if consumed.rowcount != 1:
                    raise AuthDomainError("invitation_unavailable")
                user = UserRow(
                    id=user_id,
                    username_normalized=normalized,
                    password_hash=password_hash,
                    status="active",
                    created_at=now,
                )
                workspace = WorkspaceRow(
                    id=workspace_id,
                    owner_user_id=user_id,
                    created_at=now,
                )
                session.add_all((user, workspace))
                session.flush()
        except IntegrityError:
            raise AuthDomainError("account_unavailable") from None
        return RegistrationResult(
            user=UserAccount(
                id=user_id,
                username=normalized,
                status="active",
                created_at=now,
            ),
            workspace=Workspace(
                id=workspace_id,
                owner_user_id=user_id,
                created_at=now,
            ),
        )

    def authenticate(self, username: str, password: str) -> RegistrationResult:
        """验证账号密码并统一不存在、停用和密码错误语义。"""

        try:
            normalized = normalize_username(username)
        except AuthDomainError:
            self._passwords.dummy_verify(password)
            raise AuthDomainError("authentication_failed") from None
        with self._sessions() as session:
            user = session.scalar(
                select(UserRow).where(UserRow.username_normalized == normalized)
            )
            if user is None:
                self._passwords.dummy_verify(password)
                raise AuthDomainError("authentication_failed")
            valid = self._passwords.verify(password, user.password_hash)
            workspace = session.scalar(
                select(WorkspaceRow).where(WorkspaceRow.owner_user_id == user.id)
            )
            if not valid or user.status != "active" or workspace is None:
                raise AuthDomainError("authentication_failed")
            return RegistrationResult(
                user=self._to_user_account(user),
                workspace=self._to_workspace(workspace),
            )

    def _create_session(
        self,
        *,
        actor_type: WebActorType,
        subject_id: str,
        user_id: str | None,
        workspace_id: str,
        username: str | None,
        ttl_hours: int,
    ) -> SessionBundle:
        """创建仅在返回值中暴露明文 Token 的浏览器 Session。"""

        session_token = generate_secret_token()
        csrf_token = generate_secret_token()
        now = self._now_utc()
        row = WebSessionRow(
            id=str(uuid4()),
            token_hash=hash_secret(session_token),
            actor_type=actor_type,
            subject_id=subject_id,
            user_id=user_id,
            csrf_token_hash=hash_secret(csrf_token),
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
        )
        with self._sessions.begin() as session:
            session.add(row)
        return SessionBundle(
            session_id=row.id,
            session_token=session_token,
            csrf_token=csrf_token,
            actor_type=actor_type,
            subject_id=subject_id,
            user_id=user_id,
            workspace_id=workspace_id,
            username=username,
            expires_at=_as_utc(row.expires_at),
        )

    def create_guest_session(self, *, ttl_hours: int = 2) -> SessionBundle:
        """创建具有独立 subject 的游客 Session 和 demo 工作区绑定。"""

        subject_id = str(uuid4())
        return self._create_session(
            actor_type="guest",
            subject_id=subject_id,
            user_id=None,
            workspace_id=DEMO_WORKSPACE_ID,
            username=None,
            ttl_hours=ttl_hours,
        )

    def create_registered_session(
        self,
        registration: RegistrationResult,
        *,
        ttl_hours: int = 24,
    ) -> SessionBundle:
        """为已验证账号创建绑定用户与私有工作区的 Session。"""

        return self._create_session(
            actor_type="registered",
            subject_id=registration.user.id,
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            username=registration.user.username,
            ttl_hours=ttl_hours,
        )

    def resolve_session(self, session_token: str) -> SessionIdentity | None:
        """按 Token 摘要解析未撤销、未过期且账号有效的 Session。"""

        now = self._now_utc()
        with self._sessions() as session:
            row = session.scalar(
                select(WebSessionRow).where(
                    WebSessionRow.token_hash == hash_secret(session_token),
                    WebSessionRow.revoked_at.is_(None),
                    WebSessionRow.expires_at > now,
                )
            )
            if row is None:
                return None
            if row.actor_type == "guest":
                return SessionIdentity(
                    session_id=row.id,
                    actor_type="guest",
                    subject_id=row.subject_id,
                    user_id=None,
                    workspace_id=DEMO_WORKSPACE_ID,
                    expires_at=_as_utc(row.expires_at),
                )
            user = session.get(UserRow, row.user_id)
            workspace = session.scalar(
                select(WorkspaceRow).where(WorkspaceRow.owner_user_id == row.user_id)
            )
            if user is None or workspace is None or user.status != "active":
                return None
            return SessionIdentity(
                session_id=row.id,
                actor_type="registered",
                subject_id=row.subject_id,
                user_id=user.id,
                workspace_id=workspace.id,
                username=user.username_normalized,
                user_status=cast(UserStatus, user.status),
                expires_at=_as_utc(row.expires_at),
            )

    def rotate_csrf(self, session_token: str) -> str | None:
        """为有效 Session 轮换 CSRF Token 并只返回一次明文。"""

        identity = self.resolve_session(session_token)
        if identity is None:
            return None
        csrf_token = generate_secret_token()
        with self._sessions.begin() as session:
            row = session.get(WebSessionRow, identity.session_id)
            if row is None or row.revoked_at is not None:
                return None
            row.csrf_token_hash = hash_secret(csrf_token)
        return csrf_token

    def verify_csrf(
        self,
        session_token: str,
        csrf_token: str,
    ) -> SessionIdentity | None:
        """同时验证有效 Session 与当前 CSRF Token 摘要。"""

        identity = self.resolve_session(session_token)
        if identity is None:
            return None
        with self._sessions() as session:
            stored_hash = session.scalar(
                select(WebSessionRow.csrf_token_hash).where(
                    WebSessionRow.id == identity.session_id
                )
            )
        if stored_hash is None or not hmac.compare_digest(
            stored_hash, hash_secret(csrf_token)
        ):
            return None
        return identity

    def revoke_session(self, session_token: str) -> bool:
        """撤销指定浏览器 Session，重复撤销保持无副作用。"""

        with self._sessions.begin() as session:
            row = session.scalar(
                select(WebSessionRow).where(
                    WebSessionRow.token_hash == hash_secret(session_token)
                )
            )
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = self._now_utc()
            return True

    def create_conversation(
        self,
        *,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
    ) -> ConversationRecord:
        """创建由服务端身份绑定的随机 conversation thread。"""

        now = self._now_utc()
        row = ConversationRow(
            thread_id=str(uuid4()),
            subject_id=subject_id,
            workspace_id=workspace_id,
            access_mode=access_mode,
            title="新会话",
            lifecycle_status="active",
            history_state="complete",
            message_count=0,
            next_message_sequence=1,
            created_at=now,
            updated_at=now,
        )
        with self._sessions.begin() as session:
            session.add(row)
        return self._to_conversation(row)

    def get_authorized_conversation(
        self,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
    ) -> ConversationRecord | None:
        """仅在 thread 的身份、工作区和模式全部匹配时返回记录。"""

        with self._sessions() as session:
            row = session.scalar(
                select(ConversationRow).where(
                    ConversationRow.thread_id == thread_id,
                    ConversationRow.subject_id == subject_id,
                    ConversationRow.workspace_id == workspace_id,
                    ConversationRow.access_mode == access_mode,
                )
            )
            return self._to_conversation(row) if row is not None else None

    def touch_conversation(self, thread_id: str) -> None:
        """更新一次已授权 conversation 的最近使用时间。"""

        with self._sessions.begin() as session:
            row = session.get(ConversationRow, thread_id)
            if row is not None:
                row.updated_at = self._now_utc()

    def create_order(
        self,
        *,
        user_id: str,
        workspace_id: str,
        data: OrderCreate,
    ) -> OrderRecord:
        """在确认工作区归属后原子创建订单及可选物流。"""

        now = self._now_utc()
        order = OrderRow(
            id=str(uuid4()),
            workspace_id=workspace_id,
            order_id=data.order_id.upper(),
            user_id=user_id,
            status=data.status,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                self._require_workspace_owner(session, user_id, workspace_id)
                session.add(order)
                session.flush()
                if data.shipment is not None:
                    session.add(
                        ShipmentRow(
                            id=str(uuid4()),
                            workspace_id=workspace_id,
                            order_pk=order.id,
                            status=data.shipment.status,
                            last_event=data.shipment.last_event,
                            estimated_delivery_at=(data.shipment.estimated_delivery_at),
                            created_at=now,
                            updated_at=now,
                        )
                    )
        except IntegrityError:
            raise BusinessDataError("order_conflict") from None
        return self.get_order_record(
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=order.order_id,
        )

    def list_orders(self, *, user_id: str, workspace_id: str) -> list[OrderRecord]:
        """列出当前用户私有工作区中的全部订单与物流。"""

        with self._sessions() as session:
            self._require_workspace_owner(session, user_id, workspace_id)
            rows = session.scalars(
                select(OrderRow)
                .where(
                    OrderRow.user_id == user_id,
                    OrderRow.workspace_id == workspace_id,
                )
                .order_by(OrderRow.created_at.desc())
            ).all()
            return [self._to_order_record(session, row) for row in rows]

    def get_order_record(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> OrderRecord:
        """按用户、工作区和订单号读取私有业务事实。"""

        with self._sessions() as session:
            row = self._find_order(session, user_id, workspace_id, order_id)
            if row is None:
                raise BusinessDataError("order_not_accessible")
            return self._to_order_record(session, row)

    def update_order(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
        data: OrderUpdate,
    ) -> OrderRecord:
        """原子更新当前工作区订单和一对一物流记录。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            row = self._find_order(session, user_id, workspace_id, order_id)
            if row is None:
                raise BusinessDataError("order_not_accessible")
            changed = data.status is not None and data.status != row.status
            if data.status is not None:
                row.status = data.status
            row.updated_at = now
            shipment = session.scalar(
                select(ShipmentRow).where(ShipmentRow.order_pk == row.id)
            )
            if data.remove_shipment and shipment is not None:
                session.delete(shipment)
                changed = True
            elif data.shipment is not None:
                if shipment is None:
                    shipment = ShipmentRow(
                        id=str(uuid4()),
                        workspace_id=workspace_id,
                        order_pk=row.id,
                        status=data.shipment.status,
                        last_event=data.shipment.last_event,
                        estimated_delivery_at=data.shipment.estimated_delivery_at,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(shipment)
                    changed = True
                else:
                    changed = changed or any(
                        (
                            shipment.status != data.shipment.status,
                            shipment.last_event != data.shipment.last_event,
                            shipment.estimated_delivery_at
                            != data.shipment.estimated_delivery_at,
                        )
                    )
                    shipment.status = data.shipment.status
                    shipment.last_event = data.shipment.last_event
                    shipment.estimated_delivery_at = data.shipment.estimated_delivery_at
                    shipment.updated_at = now
            if changed:
                self._invalidate_pending_refund_actions(
                    session,
                    order_pk=row.id,
                    actor_id=user_id,
                    result_code="order_or_shipment_changed",
                    now=now,
                )
        return self.get_order_record(
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=order_id,
        )

    def delete_order(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> bool:
        """在单个事务内删除订单及其物流，越权与不存在统一失败。"""

        with self._sessions.begin() as session:
            row = self._find_order(session, user_id, workspace_id, order_id)
            if row is None:
                raise BusinessDataError("order_not_accessible")
            payment_id = session.scalar(
                select(MockPaymentRow.id).where(MockPaymentRow.order_pk == row.id)
            )
            if payment_id is not None:
                raise BusinessDataError("order_has_transaction_data")
            shipment = session.scalar(
                select(ShipmentRow).where(ShipmentRow.order_pk == row.id)
            )
            if shipment is not None:
                session.delete(shipment)
                session.flush()
            session.delete(row)
        return True

    def _invalidate_pending_refund_actions(
        self,
        session: Session,
        *,
        order_pk: str,
        actor_id: str,
        result_code: str,
        now: datetime,
    ) -> None:
        """在订单事实变化时使旧预览失效，并幂等记录脱敏审计事件。"""

        actions = session.scalars(
            select(RefundActionRow).where(
                RefundActionRow.order_pk == order_pk,
                RefundActionRow.status == "awaiting_approval",
            )
        ).all()
        for action in actions:
            action.status = "stale"
            action.updated_at = now
            event_key = f"stale:{result_code}"
            exists = session.scalar(
                select(RefundAuditEventRow.id).where(
                    RefundAuditEventRow.action_id == action.action_id,
                    RefundAuditEventRow.event_key == event_key,
                )
            )
            if exists is None:
                session.add(
                    RefundAuditEventRow(
                        id=str(uuid4()),
                        action_id=action.action_id,
                        event_key=event_key,
                        event_type="stale",
                        actor_id=actor_id,
                        result_code=result_code,
                        preview_hash=action.preview_hash,
                        created_at=now,
                    )
                )

    def quota_available(self, user_id: str, usage_date: date, limit: int) -> bool:
        """只读判断用户当日模型调用次数是否低于限制。"""

        if limit <= 0:
            return False
        with self._sessions() as session:
            row = session.get(LlmDailyUsageRow, (user_id, usage_date))
            return row is None or row.accepted_calls < limit

    def accept_llm_call(self, user_id: str, usage_date: date, limit: int) -> bool:
        """原子占用一次模型调用配额，达到上限时不增加计数。"""

        if limit <= 0:
            return False
        statement = sqlite_insert(LlmDailyUsageRow).values(
            user_id=user_id,
            usage_date=usage_date,
            accepted_calls=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=("user_id", "usage_date"),
            set_={"accepted_calls": LlmDailyUsageRow.accepted_calls + 1},
            where=LlmDailyUsageRow.accepted_calls < limit,
        )
        with self._sessions.begin() as session:
            result = session.execute(statement)
            return result.rowcount == 1

    def get_llm_usage(self, user_id: str, usage_date: date) -> LlmUsageRecord:
        """读取用户当日模型调用计数，不存在时返回零。"""

        with self._sessions() as session:
            row = session.get(LlmDailyUsageRow, (user_id, usage_date))
            return LlmUsageRecord(
                user_id=user_id,
                usage_date=usage_date,
                accepted_calls=row.accepted_calls if row is not None else 0,
            )

    def count_users(self) -> int:
        """返回账号总数，供邀请码幂等测试和 Eval 使用。"""

        from sqlalchemy import func

        with self._sessions() as session:
            return int(session.scalar(select(func.count()).select_from(UserRow)) or 0)

    def invitation_usage(self, invitation_id: str) -> int | None:
        """返回邀请码已使用次数，供事务性验证使用。"""

        with self._sessions() as session:
            row = session.get(InvitationRow, invitation_id)
            return row.used_count if row is not None else None

    def _require_workspace_owner(
        self,
        session: Session,
        user_id: str,
        workspace_id: str,
    ) -> None:
        """拒绝用户访问不属于自己的工作区。"""

        exists = session.scalar(
            select(WorkspaceRow.id).where(
                WorkspaceRow.id == workspace_id,
                WorkspaceRow.owner_user_id == user_id,
            )
        )
        if exists is None:
            raise BusinessDataError("order_not_accessible")

    def _find_order(
        self,
        session: Session,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> OrderRow | None:
        """按完整私有作用域查询订单行。"""

        return session.scalar(
            select(OrderRow).where(
                OrderRow.user_id == user_id,
                OrderRow.workspace_id == workspace_id,
                OrderRow.order_id == order_id.upper(),
            )
        )

    def _to_user_account(self, row: UserRow) -> UserAccount:
        """把账号 ORM 行转换为不含密码 Hash 的领域模型。"""

        return UserAccount(
            id=row.id,
            username=row.username_normalized,
            status=cast(UserStatus, row.status),
            created_at=_as_utc(row.created_at),
        )

    def _to_workspace(self, row: WorkspaceRow) -> Workspace:
        """把工作区 ORM 行转换为领域模型。"""

        return Workspace(
            id=row.id,
            owner_user_id=row.owner_user_id,
            created_at=_as_utc(row.created_at),
        )

    def _to_conversation(self, row: ConversationRow) -> ConversationRecord:
        """把 conversation ORM 行转换为授权领域模型。"""

        return ConversationRecord(
            thread_id=row.thread_id,
            subject_id=row.subject_id,
            workspace_id=row.workspace_id,
            access_mode=cast(WebActorType, row.access_mode),
            title=row.title,
            lifecycle_status=row.lifecycle_status,
            history_state=row.history_state,
            message_count=row.message_count,
            last_message_preview=row.last_message_preview,
            last_message_at=(
                _as_utc(row.last_message_at)
                if row.last_message_at is not None
                else None
            ),
            pending_action=row.pending_action,
            archived_at=(
                _as_utc(row.archived_at) if row.archived_at is not None else None
            ),
            deleted_at=(
                _as_utc(row.deleted_at) if row.deleted_at is not None else None
            ),
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def _to_order_record(self, session: Session, row: OrderRow) -> OrderRecord:
        """把订单及可选物流 ORM 行转换为领域模型。"""

        shipment_row = session.scalar(
            select(ShipmentRow).where(ShipmentRow.order_pk == row.id)
        )
        shipment = (
            ShipmentRecord(
                order_id=row.order_id,
                status=cast(ShipmentStatus, shipment_row.status),
                last_event=shipment_row.last_event,
                estimated_delivery_at=shipment_row.estimated_delivery_at,
                updated_at=_as_utc(shipment_row.updated_at),
            )
            if shipment_row is not None
            else None
        )
        return OrderRecord(
            order_id=row.order_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            status=cast(OrderStatus, row.status),
            shipment=shipment,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )


class SqliteBusinessGateway:
    """把私有 SQLite 业务事实暴露为只读 Graph Gateway。"""

    def __init__(self, repository: SqliteBusinessRepository) -> None:
        """保存请求外可复用且每次方法自建 Session 的业务仓库。"""

        self._repository = repository

    def get_order(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[OrderView]:
        """按可信用户与工作区联合查询订单。"""

        try:
            record = self._repository.get_order_record(
                user_id=scope.user_id,
                workspace_id=scope.workspace_id,
                order_id=order_id,
            )
        except BusinessDataError:
            return ToolResult[OrderView](
                outcome="unavailable", error_code="order_unavailable"
            )
        except SQLAlchemyError:
            return ToolResult[OrderView](
                outcome="temporarily_failed",
                error_code="order_service_unavailable",
            )
        return ToolResult[OrderView](
            outcome="found",
            value=OrderView(
                order_id=record.order_id,
                user_id=record.user_id,
                status=record.status,
            ),
        )

    def get_shipment(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[ShipmentView]:
        """按可信用户与工作区重新验证订单后查询物流。"""

        try:
            record = self._repository.get_order_record(
                user_id=scope.user_id,
                workspace_id=scope.workspace_id,
                order_id=order_id,
            )
        except BusinessDataError:
            return ToolResult[ShipmentView](
                outcome="unavailable", error_code="shipment_unavailable"
            )
        except SQLAlchemyError:
            return ToolResult[ShipmentView](
                outcome="temporarily_failed",
                error_code="logistics_service_unavailable",
            )
        if record.shipment is None:
            return ToolResult[ShipmentView](
                outcome="unavailable", error_code="shipment_unavailable"
            )
        return ToolResult[ShipmentView](
            outcome="found",
            value=ShipmentView(
                order_id=record.order_id,
                status=record.shipment.status,
                last_event=record.shipment.last_event,
                estimated_delivery_at=record.shipment.estimated_delivery_at,
            ),
        )
