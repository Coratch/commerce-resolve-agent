"""持久化演示工作区状态，并原子重建其业务基准事实。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.business_models import UserRole
from commerce_resolve.portfolio_demo import (
    PORTFOLIO_DATASET_VERSION,
    PortfolioDemoService,
)
from commerce_resolve.workspace_models import (
    DemoWorkspaceStatus,
    WorkspaceResetPlan,
    WorkspaceResetResult,
)

from .sqlalchemy_models import (
    ConversationRow,
    DemoSeedRequestRow,
    L2SupportCaseRow,
    MockPaymentRow,
    MockRefundRow,
    OrderRow,
    RefundActionRow,
    RefundAuditEventRow,
    UserRow,
    WorkspaceResetAuditRow,
    WorkspaceRow,
    utc_now,
)


class WorkspaceDataError(ValueError):
    """表示可映射为稳定 API 语义的演示工作区失败。"""

    def __init__(self, error_code: str) -> None:
        """保存稳定错误码，不向客户端暴露数据库实现。"""

        super().__init__(error_code)
        self.error_code = error_code


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqliteWorkspaceRepository:
    """管理版本化演示工作区的状态、重置事务和脱敏审计。"""

    def __init__(
        self,
        engine: Engine,
        *,
        portfolio_service: PortfolioDemoService | None = None,
    ) -> None:
        """保存共享 Engine 与可替换演示数据服务。"""

        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._portfolio = portfolio_service or PortfolioDemoService()

    def get_status(
        self,
        *,
        user_id: str,
        workspace_id: str,
    ) -> DemoWorkspaceStatus | None:
        """按所有者作用域返回工作区版本与基准订单数量。"""

        with self._sessions() as session:
            workspace = session.scalar(
                select(WorkspaceRow).where(
                    WorkspaceRow.id == workspace_id,
                    WorkspaceRow.owner_user_id == user_id,
                )
            )
            if workspace is None:
                return None
            order_count = session.scalar(
                select(func.count(OrderRow.id)).where(
                    OrderRow.workspace_id == workspace_id
                )
            )
            return DemoWorkspaceStatus(
                workspace_id=workspace.id,
                owner_user_id=workspace.owner_user_id,
                dataset_version=workspace.dataset_version,
                dataset_status=workspace.dataset_status,
                reset_generation=workspace.reset_generation,
                order_count=int(order_count or 0),
                initialized_at=(
                    _as_utc(workspace.initialized_at)
                    if workspace.initialized_at is not None
                    else None
                ),
            )

    def prepare_reset(
        self,
        *,
        user_id: str,
        workspace_id: str,
        client_request_id: str,
    ) -> WorkspaceResetPlan:
        """锁定目标工作区并返回重置外部 Store 所需的稳定计划。"""

        with self._sessions.begin() as session:
            workspace = session.scalar(
                select(WorkspaceRow).where(
                    WorkspaceRow.id == workspace_id,
                    WorkspaceRow.owner_user_id == user_id,
                )
            )
            if workspace is None:
                raise WorkspaceDataError("workspace_not_accessible")
            completed = session.scalar(
                select(WorkspaceResetAuditRow).where(
                    WorkspaceResetAuditRow.workspace_id == workspace_id,
                    WorkspaceResetAuditRow.client_request_id == client_request_id,
                    WorkspaceResetAuditRow.result == "succeeded",
                )
            )
            order_ids = self._portfolio.existing_order_ids(
                session,
                workspace_id=workspace_id,
            )
            if completed is not None:
                return WorkspaceResetPlan(
                    workspace_id=workspace_id,
                    owner_user_id=user_id,
                    client_request_id=client_request_id,
                    generation=completed.generation,
                    thread_ids=(),
                    order_ids=order_ids,
                    already_completed=True,
                )
            if workspace.dataset_status == "resetting":
                if workspace.active_reset_request_id != client_request_id:
                    raise WorkspaceDataError("workspace_reset_in_progress")
            else:
                workspace.dataset_status = "resetting"
                workspace.reset_generation += 1
                workspace.active_reset_request_id = client_request_id
            thread_ids = tuple(
                session.scalars(
                    select(ConversationRow.thread_id).where(
                        ConversationRow.workspace_id == workspace_id
                    )
                ).all()
            )
            return WorkspaceResetPlan(
                workspace_id=workspace_id,
                owner_user_id=user_id,
                client_request_id=client_request_id,
                generation=workspace.reset_generation,
                thread_ids=thread_ids,
                order_ids=order_ids,
            )

    def complete_reset(
        self,
        plan: WorkspaceResetPlan,
        *,
        actor_user_id: str,
        actor_role: UserRole,
    ) -> WorkspaceResetResult:
        """原子清除派生事实、重建基准数据并记录成功审计。"""

        if plan.already_completed:
            existing = self.get_status(
                user_id=plan.owner_user_id,
                workspace_id=plan.workspace_id,
            )
            if existing is None or existing.initialized_at is None:
                raise WorkspaceDataError("workspace_reset_state_invalid")
            return WorkspaceResetResult(
                workspace_id=plan.workspace_id,
                dataset_version=PORTFOLIO_DATASET_VERSION,
                reset_generation=plan.generation,
                order_ids=tuple(sorted(plan.order_ids.values())),
                completed_at=existing.initialized_at,
                already_completed=True,
            )
        now = utc_now()
        with self._sessions.begin() as session:
            workspace = session.get(WorkspaceRow, plan.workspace_id)
            actor = session.get(UserRow, actor_user_id)
            if (
                workspace is None
                or workspace.owner_user_id != plan.owner_user_id
                or actor is None
                or workspace.dataset_status != "resetting"
                or workspace.active_reset_request_id != plan.client_request_id
                or workspace.reset_generation != plan.generation
            ):
                raise WorkspaceDataError("workspace_reset_state_invalid")
            self._delete_workspace_facts(session, plan.workspace_id)
            seed = self._portfolio.seed_into_session(
                session,
                user_id=plan.owner_user_id,
                workspace_id=plan.workspace_id,
                order_ids=plan.order_ids or None,
                now=now,
            )
            workspace.active_reset_request_id = None
            session.add(
                WorkspaceResetAuditRow(
                    reset_id=str(uuid4()),
                    workspace_id=plan.workspace_id,
                    actor_user_id=actor_user_id,
                    actor_role=actor_role,
                    client_request_id=plan.client_request_id,
                    dataset_version=seed.dataset_version,
                    generation=plan.generation,
                    result="succeeded",
                    created_at=now,
                )
            )
        return WorkspaceResetResult(
            workspace_id=plan.workspace_id,
            dataset_version=PORTFOLIO_DATASET_VERSION,
            reset_generation=plan.generation,
            order_ids=tuple(sorted(seed.order_ids.values())),
            completed_at=_as_utc(now),
        )

    @staticmethod
    def _delete_workspace_facts(session: Session, workspace_id: str) -> None:
        """按外键依赖顺序清除工作区业务与会话事实。"""

        action_ids = select(RefundActionRow.action_id).where(
            RefundActionRow.workspace_id == workspace_id
        )
        session.execute(
            delete(RefundAuditEventRow).where(
                RefundAuditEventRow.action_id.in_(action_ids)
            )
        )
        session.execute(
            delete(MockRefundRow).where(MockRefundRow.workspace_id == workspace_id)
        )
        session.execute(
            delete(RefundActionRow).where(RefundActionRow.workspace_id == workspace_id)
        )
        session.execute(
            delete(L2SupportCaseRow).where(
                L2SupportCaseRow.workspace_id == workspace_id
            )
        )
        session.execute(
            delete(ConversationRow).where(ConversationRow.workspace_id == workspace_id)
        )
        session.execute(
            delete(DemoSeedRequestRow).where(
                DemoSeedRequestRow.workspace_id == workspace_id
            )
        )
        session.execute(
            delete(MockPaymentRow).where(MockPaymentRow.workspace_id == workspace_id)
        )
        session.execute(delete(OrderRow).where(OrderRow.workspace_id == workspace_id))

    def get_owner_for_admin(self, user_id: str) -> tuple[str, str] | None:
        """返回管理员重置目标的用户和工作区标识，不扩大角色权限。"""

        with self._sessions() as session:
            result = session.execute(
                select(UserRow.id, WorkspaceRow.id)
                .join(WorkspaceRow, WorkspaceRow.owner_user_id == UserRow.id)
                .where(UserRow.id == user_id)
            ).one_or_none()
            if result is None:
                return None
            return str(result[0]), str(result[1])
