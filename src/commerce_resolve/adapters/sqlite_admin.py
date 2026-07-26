"""提供管理员角色、运营只读投影和后台审计的 SQLite Repository。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_resolve.admin_models import (
    AdminAuditRecord,
    AdminCustomer,
    AdminInvitation,
    AdminRunDetail,
    AdminRunDiagnostics,
    AdminRunEvent,
    AdminRunSummary,
)
from commerce_resolve.auth import AuthDomainError, normalize_username
from commerce_resolve.business_models import UserRole

from .sqlalchemy_models import (
    AdminActionAuditRow,
    AgentRunEventRow,
    AgentRunRow,
    InvitationRow,
    L2CaseEventRow,
    L2SupportCaseRow,
    OrderRow,
    UserRow,
    WorkspaceRow,
    utc_now,
)


class AdminDataError(ValueError):
    """表示可安全映射为运营 API 错误码的数据失败。"""

    def __init__(self, error_code: str) -> None:
        """保存稳定错误码，不暴露 SQL 或资源存在性细节。"""

        super().__init__(error_code)
        self.error_code = error_code


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _duration_ms(row: AgentRunRow) -> int | None:
    """根据已有开始和结束时间计算非负 Run 时长。"""

    if row.started_at is None or row.completed_at is None:
        return None
    return max(
        0,
        int(
            (_as_utc(row.completed_at) - _as_utc(row.started_at)).total_seconds() * 1000
        ),
    )


class SqliteAdminRepository:
    """封装管理员授权、后台目录、审计和脱敏 Monitoring 查询。"""

    def __init__(self, engine: Engine) -> None:
        """保存共享 Engine，并让每次调用使用独立 Session。"""

        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def set_role(self, username: str, role: UserRole) -> AdminCustomer:
        """按规范化用户名显式授予或撤销管理员角色。"""

        try:
            normalized = normalize_username(username)
        except AuthDomainError:
            raise AdminDataError("admin_target_unavailable") from None
        with self._sessions.begin() as session:
            row = session.scalar(
                select(UserRow).where(UserRow.username_normalized == normalized)
            )
            if row is None:
                raise AdminDataError("admin_target_unavailable")
            row.role = role
        target = self.get_customer(row.id)
        if target is None:
            raise AdminDataError("admin_target_unavailable")
        return target

    def list_customers(self, *, limit: int = 100) -> list[AdminCustomer]:
        """列出有限客户标识、角色、工作区和订单数量。"""

        bounded = max(1, min(limit, 200))
        with self._sessions() as session:
            rows = session.execute(
                select(UserRow, WorkspaceRow)
                .join(WorkspaceRow, WorkspaceRow.owner_user_id == UserRow.id)
                .order_by(UserRow.created_at.desc(), UserRow.id.desc())
                .limit(bounded)
            ).all()
            return [
                self._to_customer(session, user, workspace) for user, workspace in rows
            ]

    def get_customer(self, user_id: str) -> AdminCustomer | None:
        """读取一个可作为后台显式目标的注册账号与工作区。"""

        with self._sessions() as session:
            result = session.execute(
                select(UserRow, WorkspaceRow)
                .join(WorkspaceRow, WorkspaceRow.owner_user_id == UserRow.id)
                .where(UserRow.id == user_id)
            ).one_or_none()
            if result is None:
                return None
            user, workspace = result
            return self._to_customer(session, user, workspace)

    def list_invitations(self, *, limit: int = 100) -> list[AdminInvitation]:
        """列出邀请码有限状态，不读取无法恢复的明文或公开 Hash。"""

        bounded = max(1, min(limit, 200))
        with self._sessions() as session:
            rows = session.scalars(
                select(InvitationRow)
                .order_by(InvitationRow.created_at.desc(), InvitationRow.id.desc())
                .limit(bounded)
            ).all()
            return [
                AdminInvitation(
                    invitation_id=row.id,
                    expires_at=_as_utc(row.expires_at),
                    max_uses=row.max_uses,
                    used_count=row.used_count,
                    revoked=row.revoked_at is not None,
                    created_at=_as_utc(row.created_at),
                )
                for row in rows
            ]

    def record_action(
        self,
        *,
        admin_user_id: str,
        target_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        result: str,
        parameter_summary: dict[str, Any],
    ) -> AdminAuditRecord:
        """持久化一条不含凭证、消息正文和邀请码明文的后台动作摘要。"""

        if result not in {"succeeded", "failed"}:
            raise ValueError("invalid admin audit result")
        now = utc_now()
        row = AdminActionAuditRow(
            audit_id=str(uuid4()),
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            action=action[:64],
            resource_type=resource_type[:32],
            resource_id=resource_id[:64] if resource_id else None,
            result=result,
            parameter_summary_json=json.dumps(
                parameter_summary,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            created_at=now,
        )
        with self._sessions.begin() as session:
            session.add(row)
        return self._to_audit(row)

    def list_audit(self, *, limit: int = 100) -> list[AdminAuditRecord]:
        """按时间倒序返回有界后台动作审计。"""

        bounded = max(1, min(limit, 200))
        with self._sessions() as session:
            rows = session.scalars(
                select(AdminActionAuditRow)
                .order_by(
                    AdminActionAuditRow.created_at.desc(),
                    AdminActionAuditRow.audit_id.desc(),
                )
                .limit(bounded)
            ).all()
            return [self._to_audit(row) for row in rows]

    def list_runs(
        self,
        *,
        status: str | None = None,
        request_kind: str | None = None,
        started_after: datetime | None = None,
        limit: int = 50,
    ) -> list[AdminRunSummary]:
        """按有限条件读取 Run 元数据，不连接客户消息正文。"""

        bounded = max(1, min(limit, 100))
        with self._sessions() as session:
            statement = select(AgentRunRow)
            if status is not None:
                statement = statement.where(AgentRunRow.status == status)
            if request_kind is not None:
                statement = statement.where(AgentRunRow.request_kind == request_kind)
            if started_after is not None:
                statement = statement.where(AgentRunRow.created_at >= started_after)
            rows = session.scalars(
                statement.order_by(
                    AgentRunRow.created_at.desc(), AgentRunRow.run_id.desc()
                ).limit(bounded)
            ).all()
            return [self._to_run(row) for row in rows]

    def get_run_detail(self, run_id: str) -> AdminRunDetail | None:
        """读取 Run 的事件白名单和同线程最近 L2 诊断，不执行任何任务。"""

        with self._sessions() as session:
            row = session.get(AgentRunRow, run_id)
            if row is None:
                return None
            event_rows = session.scalars(
                select(AgentRunEventRow)
                .where(AgentRunEventRow.run_id == run_id)
                .order_by(AgentRunEventRow.event_id)
            ).all()
            case = session.scalar(
                select(L2SupportCaseRow)
                .where(L2SupportCaseRow.thread_id == row.thread_id)
                .order_by(L2SupportCaseRow.updated_at.desc())
                .limit(1)
            )
            diagnostics = self._to_diagnostics(session, case) if case else None
            return AdminRunDetail(
                run=self._to_run(row),
                events=tuple(self._to_event(item) for item in event_rows),
                diagnostics=diagnostics,
            )

    def overview_counts(self) -> dict[str, int]:
        """返回运营概览所需的有限权威计数。"""

        with self._sessions() as session:
            return {
                "customers": int(
                    session.scalar(select(func.count()).select_from(UserRow)) or 0
                ),
                "orders": int(
                    session.scalar(select(func.count()).select_from(OrderRow)) or 0
                ),
                "active_runs": int(
                    session.scalar(
                        select(func.count())
                        .select_from(AgentRunRow)
                        .where(
                            AgentRunRow.status.in_(
                                ("accepted", "running", "waiting_action")
                            )
                        )
                    )
                    or 0
                ),
                "active_cases": int(
                    session.scalar(
                        select(func.count())
                        .select_from(L2SupportCaseRow)
                        .where(
                            L2SupportCaseRow.status.in_(
                                (
                                    "l2_active",
                                    "l2_waiting_user",
                                    "l2_waiting_approval",
                                )
                            )
                        )
                    )
                    or 0
                ),
            }

    def _to_customer(
        self,
        session,
        user: UserRow,
        workspace: WorkspaceRow,
    ) -> AdminCustomer:
        """把账号和工作区行投影为有限客户目录记录。"""

        order_count = int(
            session.scalar(
                select(func.count())
                .select_from(OrderRow)
                .where(
                    OrderRow.user_id == user.id,
                    OrderRow.workspace_id == workspace.id,
                )
            )
            or 0
        )
        return AdminCustomer(
            user_id=user.id,
            username=user.username_normalized,
            status=cast(Any, user.status),
            role=cast(Any, user.role),
            workspace_id=workspace.id,
            dataset_version=workspace.dataset_version,
            dataset_status=cast(Any, workspace.dataset_status),
            reset_generation=workspace.reset_generation,
            order_count=order_count,
            initialized_at=(
                _as_utc(workspace.initialized_at)
                if workspace.initialized_at is not None
                else None
            ),
            created_at=_as_utc(user.created_at),
        )

    def _to_audit(self, row: AdminActionAuditRow) -> AdminAuditRecord:
        """把后台审计行转换为严格脱敏领域模型。"""

        return AdminAuditRecord(
            audit_id=row.audit_id,
            admin_user_id=row.admin_user_id,
            target_user_id=row.target_user_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            result=cast(Any, row.result),
            parameter_summary=json.loads(row.parameter_summary_json),
            created_at=_as_utc(row.created_at),
        )

    def _to_run(self, row: AgentRunRow) -> AdminRunSummary:
        """把 Run 行投影为不含 thread、请求 Hash 和消息正文的摘要。"""

        return AdminRunSummary(
            run_id=row.run_id,
            request_kind=row.request_kind,
            status=row.status,
            pending_action=row.pending_action,
            public_error_code=row.public_error_code,
            created_at=_as_utc(row.created_at),
            started_at=_as_utc(row.started_at) if row.started_at else None,
            completed_at=_as_utc(row.completed_at) if row.completed_at else None,
            duration_ms=_duration_ms(row),
        )

    def _to_event(self, row: AgentRunEventRow) -> AdminRunEvent:
        """只从公开事件 Payload 取明确允许的运营字段。"""

        try:
            payload = json.loads(row.payload_json)
        except (TypeError, ValueError):
            payload = {}
        return AdminRunEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            phase=payload.get("phase")
            if isinstance(payload.get("phase"), str)
            else None,
            pending_action=(
                payload.get("pending_action")
                if isinstance(payload.get("pending_action"), str)
                else None
            ),
            error_code=(
                payload.get("error_code")
                if isinstance(payload.get("error_code"), str)
                else None
            ),
            retryable=(
                payload.get("retryable")
                if isinstance(payload.get("retryable"), bool)
                else None
            ),
            created_at=_as_utc(row.created_at),
        )

    def _to_diagnostics(
        self,
        session,
        row: L2SupportCaseRow,
    ) -> AdminRunDiagnostics:
        """从 L2 公开事件聚合去重工具类别和有限用量。"""

        categories = tuple(
            sorted(
                {
                    item
                    for item in session.scalars(
                        select(L2CaseEventRow.tool_category).where(
                            L2CaseEventRow.case_id == row.case_id,
                            L2CaseEventRow.tool_category.is_not(None),
                        )
                    ).all()
                    if item
                }
            )
        )
        return AdminRunDiagnostics(
            case_id=row.case_id,
            status=row.status,
            steps_used=row.steps_used,
            model_calls_used=row.model_calls_used,
            tool_calls_used=row.tool_calls_used,
            estimated_tokens_used=row.estimated_tokens_used,
            active_milliseconds=row.active_milliseconds,
            stop_reason=row.stop_reason,
            failure_attribution=row.failure_attribution,
            tool_categories=categories,
        )
