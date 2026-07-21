"""提供 v0.6 会话生命周期、公开历史、Agent Run 与 SSE API。"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import StreamingResponse

from commerce_resolve.adapters.sqlite_conversations import ConversationDataError
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.conversation_models import AgentRunEvent, ConversationSummary
from commerce_resolve.conversation_runtime import ConversationRuntime
from commerce_resolve.workflow import build_workflow

from ..dependencies import (
    RequestAccess,
    get_services,
    require_mutation_access,
    resolve_request_access,
)
from ..errors import api_error
from ..schemas import (
    AgentRunResponse,
    AsyncChatMessageRequest,
    ConversationDetailResponse,
    ConversationLifecycleRequest,
    ConversationListResponse,
    ConversationMessagesResponse,
    PublicAgentRun,
    RetryRunRequest,
    RunAcceptedResponse,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """把不透明列表游标解码为更新时间和 thread 联合键。"""

    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        timestamp, thread_id = raw.split("|", maxsplit=1)
        return datetime.fromisoformat(timestamp), thread_id
    except (ValueError, UnicodeError):
        raise api_error(422, "invalid_cursor") from None


def _encode_cursor(summary: ConversationSummary | None) -> str | None:
    """把最后一条会话摘要编码为下一页不透明游标。"""

    if summary is None:
        return None
    raw = f"{summary.updated_at.isoformat()}|{summary.thread_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _authorized_summary(
    request: Request,
    access: RequestAccess,
    thread_id: str,
) -> ConversationSummary:
    """按当前 Session 的完整作用域读取非删除会话。"""

    repository = get_services(request).require_conversation_repository()
    summary = repository.get_conversation(
        thread_id=thread_id,
        subject_id=access.identity.subject_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
    )
    if summary is None or summary.lifecycle_status in {"deleting", "deleted"}:
        raise api_error(404, "conversation_not_accessible")
    return summary


def _map_data_error(error: ConversationDataError) -> Exception:
    """把会话 Repository 错误映射为稳定 HTTP 语义。"""

    status = {
        "conversation_not_accessible": 404,
        "run_not_accessible": 404,
        "conversation_not_active": 409,
        "pending_action_required": 409,
        "thread_busy": 409,
        "client_request_conflict": 409,
        "invalid_lifecycle_transition": 409,
    }.get(error.error_code, 409)
    return api_error(status, error.error_code)


def _checkpoint_has_pending_action(request: Request, thread_id: str) -> bool:
    """复核 Checkpoint 中的持久中断，防止归档或删除待处理动作。"""

    services = get_services(request)
    with open_sqlite_checkpointer(services.settings.checkpoint_db_path) as checkpointer:
        graph = build_workflow(
            services.refund_resume_dependencies(),
            checkpointer=checkpointer,
        )
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    return bool(snapshot.interrupts)


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    request: Request,
    lifecycle_status: str = Query(default="active", pattern="^(active|archived)$"),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> ConversationListResponse:
    """列出当前账号或游客 Session 自己的活动/归档会话。"""

    access = resolve_request_access(request)
    if access.principal.mode == "guest" and lifecycle_status != "active":
        raise api_error(403, "guest_conversation_scope")
    items = (
        get_services(request)
        .require_conversation_repository()
        .list_conversations(
            subject_id=access.identity.subject_id,
            workspace_id=access.principal.workspace_id,
            access_mode=access.principal.mode,
            lifecycle_status=lifecycle_status,  # type: ignore[arg-type]
            limit=limit,
            before=_decode_cursor(cursor),
        )
    )
    return ConversationListResponse(
        conversations=tuple(items),
        next_cursor=_encode_cursor(items[-1]) if len(items) == limit else None,
    )


@router.get("/{thread_id}", response_model=ConversationDetailResponse)
def get_conversation(thread_id: str, request: Request) -> ConversationDetailResponse:
    """返回当前身份可访问的会话摘要与待处理状态。"""

    access = resolve_request_access(request)
    return ConversationDetailResponse(
        conversation=_authorized_summary(request, access, thread_id)
    )


@router.get(
    "/{thread_id}/messages",
    response_model=ConversationMessagesResponse,
)
def list_messages(
    thread_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> ConversationMessagesResponse:
    """分页返回持久公开历史，不读取或反序列化 Checkpoint 消息。"""

    access = resolve_request_access(request)
    summary = _authorized_summary(request, access, thread_id)
    items = (
        get_services(request)
        .require_conversation_repository()
        .list_messages(
            thread_id=thread_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    )
    return ConversationMessagesResponse(
        messages=tuple(items),
        history_state=summary.history_state,
        next_after_sequence=(items[-1].sequence_no if len(items) == limit else None),
    )


@router.post(
    "/{thread_id}/messages",
    response_model=RunAcceptedResponse,
    status_code=202,
)
def submit_message(
    thread_id: str,
    payload: AsyncChatMessageRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RunAcceptedResponse:
    """原子接受消息后独立执行 Graph，并立即返回可查询 Run。"""

    services = get_services(request)
    access = require_mutation_access(request)
    try:
        accepted = services.require_conversation_repository().accept_chat_message(
            thread_id=thread_id,
            subject_id=access.identity.subject_id,
            workspace_id=access.principal.workspace_id,
            access_mode=access.principal.mode,
            client_request_id=payload.client_message_id,
            message=payload.message,
        )
    except ConversationDataError as error:
        raise _map_data_error(error) from None
    if not accepted.reused and accepted.run.status == "accepted":
        background_tasks.add_task(
            ConversationRuntime(services).execute_chat_run,
            access,
            accepted,
        )
    return RunAcceptedResponse(
        run=PublicAgentRun.from_domain(accepted.run),
        user_message=accepted.message,
        reused=accepted.reused,
    )


@router.get("/{thread_id}/runs/{run_id}", response_model=AgentRunResponse)
def get_run(thread_id: str, run_id: str, request: Request) -> AgentRunResponse:
    """读取授权会话中指定 Run 的当前公开状态。"""

    access = resolve_request_access(request)
    _authorized_summary(request, access, thread_id)
    run = (
        get_services(request)
        .require_conversation_repository()
        .get_run(
            run_id,
            thread_id=thread_id,
        )
    )
    if run is None:
        raise api_error(404, "run_not_accessible")
    return AgentRunResponse(run=PublicAgentRun.from_domain(run))


@router.post(
    "/{thread_id}/runs/{run_id}/retry",
    response_model=RunAcceptedResponse,
    status_code=202,
)
def retry_run(
    thread_id: str,
    run_id: str,
    payload: RetryRunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RunAcceptedResponse:
    """仅对 failed/interrupted Run 创建一次显式、幂等的新执行。"""

    services = get_services(request)
    access = require_mutation_access(request)
    _authorized_summary(request, access, thread_id)
    repository = services.require_conversation_repository()
    original = repository.get_run(run_id, thread_id=thread_id)
    input_message = repository.get_user_message_for_run(
        run_id=run_id,
        thread_id=thread_id,
    )
    if (
        original is None
        or original.status not in {"failed", "interrupted"}
        or input_message is None
    ):
        raise api_error(409, "run_not_retryable")
    try:
        accepted = repository.accept_action(
            thread_id=thread_id,
            subject_id=access.identity.subject_id,
            workspace_id=access.principal.workspace_id,
            access_mode=access.principal.mode,
            client_request_id=payload.client_message_id,
            request_kind="retry",
            label="重试上一条请求",
            request_payload={"retry_of_run_id": run_id},
            retry_of_run_id=run_id,
        )
    except ConversationDataError as error:
        raise _map_data_error(error) from None
    if not accepted.reused and accepted.run.status == "accepted":
        background_tasks.add_task(
            ConversationRuntime(services).execute_chat_run,
            access,
            accepted,
            input_message.content,
        )
    return RunAcceptedResponse(
        run=PublicAgentRun.from_domain(accepted.run),
        user_message=accepted.message,
        reused=accepted.reused,
    )


def _sse_record(event: AgentRunEvent) -> str:
    """把公开事件编码为标准 SSE 记录，不包含未过滤内部字段。"""

    data = json.dumps(
        {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "event_type": event.event_type,
            "payload_version": event.payload_version,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        },
        ensure_ascii=False,
    )
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/{thread_id}/runs/{run_id}/events")
def stream_run_events(
    thread_id: str,
    run_id: str,
    request: Request,
    after_event_id: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """重放缺失事件并跟随当前 Run，终态送达后关闭 SSE。"""

    access = resolve_request_access(request)
    _authorized_summary(request, access, thread_id)
    repository = get_services(request).require_conversation_repository()
    run = repository.get_run(run_id, thread_id=thread_id)
    if run is None:
        raise api_error(404, "run_not_accessible")
    header = request.headers.get("last-event-id")
    try:
        cursor = (
            max(after_event_id, int(header)) if header is not None else after_event_id
        )
    except ValueError:
        raise api_error(422, "invalid_event_cursor") from None

    def generate() -> Iterator[str]:
        """轮询持久事件，发送 heartbeat，并在 Run 终态后结束。"""

        current = cursor
        idle_polls = 0
        while True:
            if (
                get_services(request).repository.resolve_session(access.session_token)
                is None
            ):
                return
            events = repository.list_events(
                run_id=run_id,
                after_event_id=current,
            )
            for event in events:
                current = event.event_id
                idle_polls = 0
                yield _sse_record(event)
            current_run = repository.get_run(run_id, thread_id=thread_id)
            if current_run is None or current_run.status in {
                "waiting_action",
                "completed",
                "failed",
                "interrupted",
            }:
                if not repository.list_events(
                    run_id=run_id,
                    after_event_id=current,
                    limit=1,
                ):
                    return
            idle_polls += 1
            if idle_polls % 50 == 0:
                yield ": heartbeat\n\n"
            time.sleep(0.1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/{thread_id}", response_model=ConversationDetailResponse)
def update_lifecycle(
    thread_id: str,
    payload: ConversationLifecycleRequest,
    request: Request,
) -> ConversationDetailResponse:
    """允许注册用户归档或恢复本人会话，游客不维护归档列表。"""

    access = require_mutation_access(request)
    if access.principal.mode != "registered":
        raise api_error(403, "guest_conversation_scope")
    if payload.lifecycle_status == "archived" and _checkpoint_has_pending_action(
        request, thread_id
    ):
        raise api_error(409, "pending_action_required")
    try:
        summary = (
            get_services(request)
            .require_conversation_repository()
            .set_lifecycle(
                thread_id=thread_id,
                subject_id=access.identity.subject_id,
                workspace_id=access.principal.workspace_id,
                access_mode=access.principal.mode,
                lifecycle_status=payload.lifecycle_status,
            )
        )
    except ConversationDataError as error:
        raise _map_data_error(error) from None
    return ConversationDetailResponse(conversation=summary)


@router.delete("/{thread_id}", status_code=204)
def delete_conversation(thread_id: str, request: Request) -> Response:
    """先写 deleting 墓碑，再清理 Checkpoint 和公开交互数据。"""

    services = get_services(request)
    access = require_mutation_access(request)
    repository = services.require_conversation_repository()
    _authorized_summary(request, access, thread_id)
    if _checkpoint_has_pending_action(request, thread_id):
        raise api_error(409, "pending_action_required")
    try:
        repository.begin_delete(
            thread_id=thread_id,
            subject_id=access.identity.subject_id,
            workspace_id=access.principal.workspace_id,
            access_mode=access.principal.mode,
        )
    except ConversationDataError as error:
        raise _map_data_error(error) from None
    try:
        with open_sqlite_checkpointer(
            services.settings.checkpoint_db_path
        ) as checkpointer:
            checkpointer.delete_thread(thread_id)
        repository.finish_delete(thread_id)
    except Exception:
        raise api_error(503, "conversation_deletion_incomplete") from None
    return Response(status_code=204)
