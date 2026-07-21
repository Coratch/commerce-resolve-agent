"""实现 v0.6 公开会话、消息、Run 与事件的 SQLite Repository。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Engine, delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.business_models import WebActorType
from commerce_resolve.conversation_models import (
    AcceptedRun,
    AgentRun,
    AgentRunEvent,
    ConversationLifecycle,
    ConversationMessage,
    ConversationSummary,
    MessageKind,
    MessageRole,
    MessageStatus,
    RunEventType,
    RunKind,
    RunStatus,
)
from commerce_resolve.l2_models import L2ContextPublicMessage

from .sqlalchemy_models import (
    AgentRunEventRow,
    AgentRunRow,
    ConversationMessageRow,
    ConversationRow,
    WorkspaceRow,
    utc_now,
)


class ConversationDataError(ValueError):
    """表示可稳定映射为公开 API 错误码的会话数据错误。"""

    def __init__(self, error_code: str) -> None:
        """保存错误码，并避免向上层暴露 SQL 或本地路径。"""

        super().__init__(error_code)
        self.error_code = error_code


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json(value: dict[str, Any]) -> str:
    """把已过滤的公开 Payload 序列化为稳定 JSON。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _request_hash(request_kind: RunKind, payload: dict[str, Any]) -> str:
    """为幂等请求计算不包含密钥的稳定 SHA-256 摘要。"""

    canonical = f"{request_kind}:{_json(payload)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _title_from_message(message: str) -> str:
    """从首条用户消息生成确定性、单行且有界的会话标题。"""

    normalized = " ".join(message.split())
    return normalized[:36] or "新会话"


def _preview(content: str) -> str:
    """生成不超过 120 字的单行消息列表摘要。"""

    return " ".join(content.split())[:120]


class SqliteConversationRepository:
    """集中维护公开对话投影及 Agent Run 的事务一致性。"""

    def __init__(self, engine: Engine) -> None:
        """保存 Engine，并为每次操作创建独立 SQLAlchemy Session。"""

        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def list_conversations(
        self,
        *,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
        lifecycle_status: ConversationLifecycle = "active",
        limit: int = 20,
        before: tuple[datetime, str] | None = None,
    ) -> list[ConversationSummary]:
        """按身份作用域和稳定倒序游标列出会话，不公开墓碑。"""

        bounded = max(1, min(limit, 50))
        with self._sessions() as session:
            statement = select(ConversationRow).where(
                ConversationRow.subject_id == subject_id,
                ConversationRow.workspace_id == workspace_id,
                ConversationRow.access_mode == access_mode,
                ConversationRow.lifecycle_status == lifecycle_status,
            )
            if before is not None:
                before_time, before_thread = before
                statement = statement.where(
                    or_(
                        ConversationRow.updated_at < before_time,
                        (
                            (ConversationRow.updated_at == before_time)
                            & (ConversationRow.thread_id < before_thread)
                        ),
                    )
                )
            rows = session.scalars(
                statement.order_by(
                    ConversationRow.updated_at.desc(),
                    ConversationRow.thread_id.desc(),
                ).limit(bounded)
            ).all()
            return [self._to_summary(row) for row in rows]

    def get_conversation(
        self,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
        include_deleted: bool = False,
    ) -> ConversationSummary | None:
        """仅在完整身份作用域匹配时返回会话公开摘要。"""

        with self._sessions() as session:
            statement = select(ConversationRow).where(
                ConversationRow.thread_id == thread_id,
                ConversationRow.subject_id == subject_id,
                ConversationRow.workspace_id == workspace_id,
                ConversationRow.access_mode == access_mode,
            )
            if not include_deleted:
                statement = statement.where(
                    ConversationRow.lifecycle_status != "deleted"
                )
            row = session.scalar(statement)
            return self._to_summary(row) if row is not None else None

    def list_messages(
        self,
        *,
        thread_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[ConversationMessage]:
        """按递增序号分页读取公开消息，不接触 Checkpoint 内部 State。"""

        bounded = max(1, min(limit, 200))
        with self._sessions() as session:
            rows = session.scalars(
                select(ConversationMessageRow)
                .where(
                    ConversationMessageRow.thread_id == thread_id,
                    ConversationMessageRow.sequence_no > max(0, after_sequence),
                )
                .order_by(ConversationMessageRow.sequence_no)
                .limit(bounded)
            ).all()
            return [self._to_message(row) for row in rows]

    def list_authorized_context_messages(
        self,
        *,
        thread_id: str,
        subject_id: str,
        user_id: str,
        workspace_id: str,
        limit: int,
    ) -> tuple[L2ContextPublicMessage, ...]:
        """在完整注册身份 SQL 约束内读取最近的公开消息候选。"""

        bounded = max(1, min(limit, 100))
        with self._sessions() as session:
            rows = session.scalars(
                select(ConversationMessageRow)
                .join(
                    ConversationRow,
                    ConversationRow.thread_id == ConversationMessageRow.thread_id,
                )
                .join(WorkspaceRow, WorkspaceRow.id == ConversationRow.workspace_id)
                .where(
                    ConversationRow.thread_id == thread_id,
                    ConversationRow.subject_id == subject_id,
                    ConversationRow.workspace_id == workspace_id,
                    ConversationRow.access_mode == "registered",
                    ConversationRow.lifecycle_status != "deleted",
                    WorkspaceRow.owner_user_id == user_id,
                    ConversationMessageRow.kind.in_(("text", "action")),
                )
                .order_by(ConversationMessageRow.sequence_no.desc())
                .limit(bounded)
            ).all()
            return tuple(
                L2ContextPublicMessage(
                    message_id=row.message_id,
                    sequence_no=row.sequence_no,
                    role=cast("MessageRole", row.role),
                    content=row.content,
                )
                for row in reversed(rows)
            )

    def accept_chat_message(
        self,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
        client_request_id: str,
        message: str,
    ) -> AcceptedRun:
        """原子持久化用户消息、幂等 Run 和首个公开事件。"""

        request_payload = {"message": message}
        digest = _request_hash("chat_message", request_payload)
        now = utc_now()
        with self._sessions.begin() as session:
            conversation = self._require_conversation(
                session,
                thread_id=thread_id,
                subject_id=subject_id,
                workspace_id=workspace_id,
                access_mode=access_mode,
            )
            if conversation.lifecycle_status != "active":
                raise ConversationDataError("conversation_not_active")
            if conversation.pending_action not in {None, "user_input"}:
                raise ConversationDataError("pending_action_required")
            existing = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.client_request_id == client_request_id,
                )
            )
            if existing is not None:
                if existing.request_hash != digest:
                    raise ConversationDataError("client_request_conflict")
                message_row = session.scalar(
                    select(ConversationMessageRow).where(
                        ConversationMessageRow.run_id == existing.run_id,
                        ConversationMessageRow.role == "user",
                    )
                )
                if message_row is None:
                    raise ConversationDataError("run_projection_incomplete")
                return AcceptedRun(
                    run=self._to_run(existing),
                    message=self._to_message(message_row),
                    reused=True,
                )
            active = session.scalar(
                select(AgentRunRow.run_id).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.status.in_(("accepted", "running")),
                )
            )
            if active is not None:
                raise ConversationDataError("thread_busy")
            run = AgentRunRow(
                run_id=str(uuid4()),
                thread_id=thread_id,
                client_request_id=client_request_id,
                request_kind="chat_message",
                request_hash=digest,
                status="accepted",
                created_at=now,
                updated_at=now,
            )
            message_row = self._append_message(
                conversation,
                run_id=run.run_id,
                role="user",
                kind="text",
                content=message,
                status="accepted",
                payload={},
                now=now,
            )
            if conversation.message_count == 1:
                conversation.title = _title_from_message(message)
            session.add_all((run, message_row))
            session.flush()
            session.add(
                AgentRunEventRow(
                    run_id=run.run_id,
                    event_key="accepted",
                    event_type="run.accepted",
                    payload_version=1,
                    payload_json=_json({"message_id": message_row.message_id}),
                    created_at=now,
                )
            )
            return AcceptedRun(
                run=self._to_run(run),
                message=self._to_message(message_row),
            )

    def accept_action(
        self,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
        client_request_id: str,
        request_kind: RunKind,
        label: str,
        request_payload: dict[str, Any],
        retry_of_run_id: str | None = None,
    ) -> AcceptedRun:
        """原子接受审批类用户动作，并按客户端标识保证公开投影幂等。"""

        if request_kind == "chat_message":
            raise ConversationDataError("invalid_request_kind")
        digest = _request_hash(request_kind, request_payload)
        now = utc_now()
        with self._sessions.begin() as session:
            conversation = self._require_conversation(
                session,
                thread_id=thread_id,
                subject_id=subject_id,
                workspace_id=workspace_id,
                access_mode=access_mode,
            )
            if conversation.lifecycle_status != "active":
                raise ConversationDataError("conversation_not_active")
            existing = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.client_request_id == client_request_id,
                )
            )
            if existing is not None:
                if existing.request_hash != digest:
                    raise ConversationDataError("client_request_conflict")
                message_row = session.scalar(
                    select(ConversationMessageRow).where(
                        ConversationMessageRow.run_id == existing.run_id,
                        ConversationMessageRow.role == "user",
                    )
                )
                if message_row is None:
                    raise ConversationDataError("run_projection_incomplete")
                return AcceptedRun(
                    run=self._to_run(existing),
                    message=self._to_message(message_row),
                    reused=True,
                )
            active = session.scalar(
                select(AgentRunRow.run_id).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.status.in_(("accepted", "running")),
                )
            )
            if active is not None:
                raise ConversationDataError("thread_busy")
            run = AgentRunRow(
                run_id=str(uuid4()),
                thread_id=thread_id,
                client_request_id=client_request_id,
                request_kind=request_kind,
                request_hash=digest,
                retry_of_run_id=retry_of_run_id,
                status="accepted",
                created_at=now,
                updated_at=now,
            )
            message_row = self._append_message(
                conversation,
                run_id=run.run_id,
                role="user",
                kind="action",
                content=label,
                status="accepted",
                payload={},
                now=now,
            )
            session.add_all((run, message_row))
            session.flush()
            session.add(
                AgentRunEventRow(
                    run_id=run.run_id,
                    event_key="accepted",
                    event_type="run.accepted",
                    payload_version=1,
                    payload_json=_json({"message_id": message_row.message_id}),
                    created_at=now,
                )
            )
            return AcceptedRun(
                run=self._to_run(run),
                message=self._to_message(message_row),
            )

    def get_user_message_for_run(
        self,
        *,
        run_id: str,
        thread_id: str,
    ) -> ConversationMessage | None:
        """读取指定 Run 绑定的用户输入，供受限显式重试复用。"""

        with self._sessions() as session:
            row = session.scalar(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.run_id == run_id,
                    ConversationMessageRow.thread_id == thread_id,
                    ConversationMessageRow.role == "user",
                )
            )
            return self._to_message(row) if row is not None else None

    def get_accepted_by_client_request(
        self,
        *,
        thread_id: str,
        client_request_id: str,
    ) -> AcceptedRun | None:
        """按 thread 和客户端请求 ID 读取既有 Run 及其用户动作消息。"""

        with self._sessions() as session:
            run = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.client_request_id == client_request_id,
                )
            )
            if run is None:
                return None
            message = session.scalar(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.run_id == run.run_id,
                    ConversationMessageRow.role == "user",
                )
            )
            if message is None:
                raise ConversationDataError("run_projection_incomplete")
            return AcceptedRun(
                run=self._to_run(run),
                message=self._to_message(message),
                reused=True,
            )

    def mark_run_started(self, run_id: str) -> AgentRun:
        """幂等把 accepted Run 转为 running 并写入启动事件。"""

        now = utc_now()
        with self._sessions.begin() as session:
            run = self._require_run(session, run_id)
            if run.status == "accepted":
                run.status = "running"
                run.started_at = now
                run.updated_at = now
            self._append_event(
                session,
                run_id=run_id,
                event_key="started",
                event_type="run.started",
                payload={"phase": "understanding"},
                now=now,
            )
            return self._to_run(run)

    def append_step_event(
        self,
        *,
        run_id: str,
        event_key: str,
        phase: str,
        message: str,
    ) -> AgentRunEvent:
        """幂等追加一个有限公开阶段事件，不保存节点 State 或原始输出。"""

        with self._sessions.begin() as session:
            self._require_run(session, run_id)
            row = self._append_event(
                session,
                run_id=run_id,
                event_key=event_key,
                event_type="step.updated",
                payload={"phase": phase, "message": message},
                now=utc_now(),
            )
            return self._to_event(row)

    def complete_run(
        self,
        *,
        run_id: str,
        assistant_message: str,
        payload: dict[str, Any],
        pending_action: str | None,
        checkpoint_id: str | None = None,
    ) -> tuple[AgentRun, ConversationMessage]:
        """原子写入助手公开投影，并把 Run 置为完成或等待动作。"""

        now = utc_now()
        with self._sessions.begin() as session:
            run = self._require_run(session, run_id)
            conversation = session.get(ConversationRow, run.thread_id)
            if conversation is None:
                raise ConversationDataError("conversation_not_accessible")
            existing = session.scalar(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.run_id == run_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            if existing is None:
                existing = self._append_message(
                    conversation,
                    run_id=run_id,
                    role="assistant",
                    kind="action" if pending_action is not None else "text",
                    content=assistant_message,
                    status="completed",
                    payload=payload,
                    now=now,
                )
                session.add(existing)
            conversation.pending_action = pending_action
            run.pending_action = pending_action
            run.checkpoint_id = checkpoint_id
            run.status = "waiting_action" if pending_action is not None else "completed"
            run.completed_at = now
            run.updated_at = now
            self._append_event(
                session,
                run_id=run_id,
                event_key="message-completed",
                event_type="message.completed",
                payload={
                    "message_id": existing.message_id,
                    "sequence_no": existing.sequence_no,
                },
                now=now,
            )
            self._append_event(
                session,
                run_id=run_id,
                event_key="terminal",
                event_type=(
                    "action.required" if pending_action is not None else "run.completed"
                ),
                payload={"pending_action": pending_action},
                now=now,
            )
            return self._to_run(run), self._to_message(existing)

    def fail_run(
        self,
        *,
        run_id: str,
        error_code: str,
        assistant_message: str,
        interrupted: bool = False,
    ) -> AgentRun:
        """保留已接受用户消息，并以普通助手消息和稳定错误码结束 Run。"""

        now = utc_now()
        with self._sessions.begin() as session:
            run = self._require_run(session, run_id)
            conversation = session.get(ConversationRow, run.thread_id)
            if conversation is None:
                raise ConversationDataError("conversation_not_accessible")
            existing = session.scalar(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.run_id == run_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            if existing is None:
                message_row = self._append_message(
                    conversation,
                    run_id=run_id,
                    role="assistant",
                    kind="status",
                    content=assistant_message,
                    status="failed",
                    payload={"error_code": error_code, "retryable": True},
                    now=now,
                )
                session.add(message_row)
            run.status = "interrupted" if interrupted else "failed"
            run.public_error_code = error_code
            run.completed_at = now
            run.updated_at = now
            self._append_event(
                session,
                run_id=run_id,
                event_key="terminal",
                event_type="run.interrupted" if interrupted else "run.failed",
                payload={"error_code": error_code, "retryable": True},
                now=now,
            )
            return self._to_run(run)

    def clear_pending_action(self, thread_id: str) -> None:
        """在待处理动作完成后清空会话摘要门禁，重复调用保持幂等。"""

        with self._sessions.begin() as session:
            conversation = session.get(ConversationRow, thread_id)
            if conversation is not None:
                conversation.pending_action = None
                conversation.updated_at = utc_now()

    def get_run(self, run_id: str, *, thread_id: str) -> AgentRun | None:
        """按 thread 与 run 联合读取，避免跨会话枚举 Run。"""

        with self._sessions() as session:
            row = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.run_id == run_id,
                    AgentRunRow.thread_id == thread_id,
                )
            )
            return self._to_run(row) if row is not None else None

    def list_events(
        self,
        *,
        run_id: str,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> list[AgentRunEvent]:
        """按递增事件 ID 重放指定 Run 的公开事件。"""

        with self._sessions() as session:
            rows = session.scalars(
                select(AgentRunEventRow)
                .where(
                    AgentRunEventRow.run_id == run_id,
                    AgentRunEventRow.event_id > max(0, after_event_id),
                )
                .order_by(AgentRunEventRow.event_id)
                .limit(max(1, min(limit, 500)))
            ).all()
            return [self._to_event(row) for row in rows]

    def set_lifecycle(
        self,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
        lifecycle_status: ConversationLifecycle,
    ) -> ConversationSummary:
        """归档或恢复无活动 Run 的会话，并记录确定性时间戳。"""

        if lifecycle_status not in {"active", "archived"}:
            raise ConversationDataError("invalid_lifecycle_transition")
        now = utc_now()
        with self._sessions.begin() as session:
            row = self._require_conversation(
                session,
                thread_id=thread_id,
                subject_id=subject_id,
                workspace_id=workspace_id,
                access_mode=access_mode,
            )
            if row.lifecycle_status in {"deleting", "deleted"}:
                raise ConversationDataError("conversation_not_accessible")
            active = session.scalar(
                select(AgentRunRow.run_id).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.status.in_(("accepted", "running")),
                )
            )
            if active is not None:
                raise ConversationDataError("thread_busy")
            row.lifecycle_status = lifecycle_status
            row.archived_at = now if lifecycle_status == "archived" else None
            row.updated_at = now
            return self._to_summary(row)

    def begin_delete(
        self,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
    ) -> ConversationSummary:
        """在外部 Checkpoint 清理前写入 deleting 墓碑，禁止继续使用。"""

        with self._sessions.begin() as session:
            row = self._require_conversation(
                session,
                thread_id=thread_id,
                subject_id=subject_id,
                workspace_id=workspace_id,
                access_mode=access_mode,
            )
            active = session.scalar(
                select(AgentRunRow.run_id).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.status.in_(("accepted", "running")),
                )
            )
            if active is not None:
                raise ConversationDataError("thread_busy")
            if row.pending_action is not None:
                raise ConversationDataError("pending_action_required")
            if row.lifecycle_status == "deleted":
                return self._to_summary(row)
            row.lifecycle_status = "deleting"
            row.updated_at = utc_now()
            return self._to_summary(row)

    def finish_delete(self, thread_id: str) -> None:
        """清空可删除公开交互数据并保留 conversation 墓碑和业务审计。"""

        now = utc_now()
        with self._sessions.begin() as session:
            row = session.get(ConversationRow, thread_id)
            if row is None:
                return
            session.execute(
                delete(ConversationMessageRow).where(
                    ConversationMessageRow.thread_id == thread_id
                )
            )
            session.execute(
                delete(AgentRunRow).where(AgentRunRow.thread_id == thread_id)
            )
            row.lifecycle_status = "deleted"
            row.deleted_at = now
            row.pending_action = None
            row.message_count = 0
            row.next_message_sequence = 1
            row.last_message_preview = None
            row.last_message_at = None
            row.updated_at = now

    def interrupt_unfinished_runs(self) -> int:
        """启动时把无法证明仍在执行的进程内 Run 标记为 interrupted。"""

        with self._sessions() as session:
            run_ids = list(
                session.scalars(
                    select(AgentRunRow.run_id).where(
                        AgentRunRow.status.in_(("accepted", "running"))
                    )
                ).all()
            )
        for run_id in run_ids:
            self.fail_run(
                run_id=run_id,
                error_code="run_interrupted",
                assistant_message="上一次处理因服务重启而中断，你可以重新提交该请求。",
                interrupted=True,
            )
        return len(run_ids)

    def _require_conversation(
        self,
        session: Session,
        *,
        thread_id: str,
        subject_id: str,
        workspace_id: str,
        access_mode: WebActorType,
    ) -> ConversationRow:
        """按完整授权作用域读取会话，否则返回统一不可访问语义。"""

        row = session.scalar(
            select(ConversationRow).where(
                ConversationRow.thread_id == thread_id,
                ConversationRow.subject_id == subject_id,
                ConversationRow.workspace_id == workspace_id,
                ConversationRow.access_mode == access_mode,
                ConversationRow.lifecycle_status != "deleted",
            )
        )
        if row is None:
            raise ConversationDataError("conversation_not_accessible")
        return row

    def _require_run(self, session: Session, run_id: str) -> AgentRunRow:
        """读取指定 Run，不存在时返回稳定领域错误。"""

        row = session.get(AgentRunRow, run_id)
        if row is None:
            raise ConversationDataError("run_not_accessible")
        return row

    def _append_message(
        self,
        conversation: ConversationRow,
        *,
        run_id: str | None,
        role: MessageRole,
        kind: MessageKind,
        content: str,
        status: MessageStatus,
        payload: dict[str, Any],
        now: datetime,
    ) -> ConversationMessageRow:
        """使用会话内单调序号追加公开消息并同步列表摘要。"""

        row = ConversationMessageRow(
            message_id=str(uuid4()),
            thread_id=conversation.thread_id,
            run_id=run_id,
            sequence_no=conversation.next_message_sequence,
            role=role,
            kind=kind,
            content=content,
            status=status,
            payload_version=1,
            payload_json=_json(payload),
            created_at=now,
            updated_at=now,
        )
        conversation.next_message_sequence += 1
        conversation.message_count += 1
        conversation.last_message_preview = _preview(content)
        conversation.last_message_at = now
        conversation.updated_at = now
        return row

    def _append_event(
        self,
        session: Session,
        *,
        run_id: str,
        event_key: str,
        event_type: RunEventType,
        payload: dict[str, Any],
        now: datetime,
    ) -> AgentRunEventRow:
        """按 run/event_key 幂等创建事件并返回持久行。"""

        existing = session.scalar(
            select(AgentRunEventRow).where(
                AgentRunEventRow.run_id == run_id,
                AgentRunEventRow.event_key == event_key,
            )
        )
        if existing is not None:
            return existing
        row = AgentRunEventRow(
            run_id=run_id,
            event_key=event_key,
            event_type=event_type,
            payload_version=1,
            payload_json=_json(payload),
            created_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def _to_summary(self, row: ConversationRow) -> ConversationSummary:
        """把会话 ORM 行转换为不含身份字段的公开摘要。"""

        return ConversationSummary(
            thread_id=row.thread_id,
            title=row.title,
            lifecycle_status=cast(ConversationLifecycle, row.lifecycle_status),
            history_state=cast(Any, row.history_state),
            message_count=row.message_count,
            last_message_preview=row.last_message_preview,
            last_message_at=(
                _as_utc(row.last_message_at)
                if row.last_message_at is not None
                else None
            ),
            pending_action=row.pending_action,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            archived_at=(
                _as_utc(row.archived_at) if row.archived_at is not None else None
            ),
        )

    def _to_message(self, row: ConversationMessageRow) -> ConversationMessage:
        """把消息 ORM 行解析为公开领域模型。"""

        return ConversationMessage(
            message_id=row.message_id,
            thread_id=row.thread_id,
            run_id=row.run_id,
            sequence_no=row.sequence_no,
            role=cast(MessageRole, row.role),
            kind=cast(MessageKind, row.kind),
            content=row.content,
            status=cast(MessageStatus, row.status),
            payload_version=row.payload_version,
            payload=json.loads(row.payload_json),
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def _to_run(self, row: AgentRunRow) -> AgentRun:
        """把 Run ORM 行转换为公开领域模型。"""

        return AgentRun(
            run_id=row.run_id,
            thread_id=row.thread_id,
            client_request_id=row.client_request_id,
            request_kind=cast(RunKind, row.request_kind),
            request_hash=row.request_hash,
            retry_of_run_id=row.retry_of_run_id,
            status=cast(RunStatus, row.status),
            pending_action=row.pending_action,
            checkpoint_id=row.checkpoint_id,
            public_error_code=row.public_error_code,
            created_at=_as_utc(row.created_at),
            started_at=_as_utc(row.started_at) if row.started_at is not None else None,
            completed_at=(
                _as_utc(row.completed_at) if row.completed_at is not None else None
            ),
            updated_at=_as_utc(row.updated_at),
        )

    def _to_event(self, row: AgentRunEventRow) -> AgentRunEvent:
        """把持久事件解析为公开领域模型。"""

        return AgentRunEvent(
            event_id=row.event_id,
            run_id=row.run_id,
            event_key=row.event_key,
            event_type=cast(RunEventType, row.event_type),
            payload_version=row.payload_version,
            payload=json.loads(row.payload_json),
            created_at=_as_utc(row.created_at),
        )
