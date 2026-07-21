"""提供受 Session 归属保护的 conversation 与 LangGraph Chat API。"""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Request
from langgraph.types import Command

from commerce_resolve.adapters.sqlite_conversations import ConversationDataError
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.conversation_models import AcceptedRun, RunKind
from commerce_resolve.conversation_projection import (
    pending_action,
    project_chat_response,
    public_message_payload,
)
from commerce_resolve.conversation_runtime import ConversationRuntime
from commerce_resolve.gateways import InterpreterUnavailableError
from commerce_resolve.l2_memory import open_sqlite_memory_store
from commerce_resolve.l2_models import L2RuntimeState, L2UpgradePreview
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow

from ..dependencies import (
    RequestAccess,
    WebServices,
    enforce_rate_limit,
    get_services,
    require_mutation_access,
    require_registered_access,
)
from ..errors import api_error
from ..schemas import (
    ChatMessageRequest,
    ChatResponse,
    ConversationResponse,
    L2MemoryDecisionRequest,
    L2UpgradeDecisionRequest,
    PendingL2Response,
    PendingRefundResponse,
    PublicAgentRun,
    PublicL2UpgradePreview,
    PublicMemoryProposal,
    PublicRefundPreview,
    RefundApprovalRequest,
    RunAcceptedResponse,
)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
def create_conversation(request: Request) -> ConversationResponse:
    """为当前服务端 Principal 创建并持久绑定随机 thread。"""

    services = get_services(request)
    access = require_mutation_access(request)
    existing = services.require_conversation_repository().list_conversations(
        subject_id=access.identity.subject_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
        limit=1,
    )
    if (
        existing
        and existing[0].message_count == 0
        and existing[0].pending_action is None
        and existing[0].history_state == "complete"
    ):
        return ConversationResponse(thread_id=existing[0].thread_id)
    conversation = services.repository.create_conversation(
        subject_id=access.identity.subject_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
    )
    return ConversationResponse(thread_id=conversation.thread_id)


def _llm_access(
    services: WebServices,
    access: RequestAccess,
    *,
    consume: bool,
) -> tuple[bool, int]:
    """验证注册用户模型能力，并按需原子占用一次一线调用额度。"""

    principal = access.principal
    if principal.user_id is None:
        raise api_error(401, "llm_not_authorized")
    usage_date = datetime.now(UTC).date()
    decision = services.llm_access_policy.decide(
        principal,
        feature_enabled=services.settings.llm_feature_enabled,
        model_configured=services.model_configured,
        quota_available=services.repository.quota_available(
            principal.user_id,
            usage_date,
            services.settings.llm_daily_call_limit,
        ),
    )
    if not decision.allowed:
        error_code = decision.error_code or "llm_not_authorized"
        status_code = (
            429
            if error_code == "llm_quota_exceeded"
            else 503
            if error_code == "llm_not_configured"
            else 403
        )
        raise api_error(status_code, error_code)
    if consume:
        accepted = services.repository.accept_llm_call(
            principal.user_id,
            usage_date,
            services.settings.llm_daily_call_limit,
        )
        if not accepted:
            raise api_error(429, "llm_quota_exceeded")
    usage = services.repository.get_llm_usage(principal.user_id, usage_date)
    remaining = max(
        0,
        services.settings.llm_daily_call_limit - usage.accepted_calls,
    )
    return True, remaining


def _registered_dependencies(
    services: WebServices,
    access: RequestAccess,
):
    """占用一线 Interpreter 额度后返回依赖和剩余共享模型额度。"""

    _, remaining = _llm_access(services, access, consume=True)
    try:
        return services.registered_dependencies(), remaining
    except (ModuleNotFoundError, ValueError):
        raise api_error(503, "llm_not_configured") from None


def _l2_resume_dependencies(
    services: WebServices,
    access: RequestAccess,
):
    """不预扣额度地验证能力，并装配只允许从 Checkpoint 恢复的依赖。"""

    _, remaining = _llm_access(services, access, consume=False)
    try:
        return services.l2_resume_dependencies(), remaining
    except (ModuleNotFoundError, ValueError):
        raise api_error(503, "llm_not_configured") from None


def _authorized_conversation(
    request: Request,
    thread_id: str,
    *,
    mutation: bool,
):
    """验证 conversation 与当前 Session 身份、工作区和模式完全绑定。"""

    services = get_services(request)
    access = require_registered_access(request, mutation=mutation)
    conversation = services.repository.get_authorized_conversation(
        thread_id=thread_id,
        subject_id=access.identity.subject_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
    )
    if conversation is None:
        raise api_error(404, "conversation_not_accessible")
    return access, conversation


def _pending_action(state: dict[str, object], next_nodes: tuple[str, ...] = ()):
    """把内部节点或 Runtime phase 映射为有限公开待处理动作。"""

    return pending_action(state, next_nodes)


def _chat_response(
    services: WebServices,
    access: RequestAccess,
    thread_id: str,
    state: dict[str, object],
    *,
    next_nodes: tuple[str, ...] = (),
) -> ChatResponse:
    """从 Graph 公开 State 构造聊天、预览和验证结果响应。"""

    try:
        return project_chat_response(
            services,
            access,
            thread_id,
            state,
            next_nodes=next_nodes,
        )
    except ValueError:
        raise api_error(503, "internal_error") from None


def _persist_legacy_exchange(
    services: WebServices,
    access: RequestAccess,
    *,
    thread_id: str,
    user_message: str,
    response: ChatResponse,
) -> ChatResponse:
    """让旧同步接口复用 v0.6 公开投影，不改变原 HTTP 返回契约。"""

    repository = services.require_conversation_repository()
    accepted = repository.accept_chat_message(
        thread_id=thread_id,
        subject_id=access.identity.subject_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
        client_request_id=f"legacy-{uuid4()}",
        message=user_message,
    )
    repository.mark_run_started(accepted.run.run_id)
    repository.complete_run(
        run_id=accepted.run.run_id,
        assistant_message=response.assistant_message,
        payload=public_message_payload(response),
        pending_action=response.l2_pending_action,
    )
    return response


def _action_client_id(prefix: str, *parts: str) -> str:
    """从服务端动作标识和决定生成稳定且有界的幂等请求 ID。"""

    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:40]
    return f"{prefix}-{digest}"


def _accept_public_action(
    services: WebServices,
    access: RequestAccess,
    *,
    thread_id: str,
    request_kind: RunKind,
    client_request_id: str,
    label: str,
    payload: dict[str, str],
):
    """在恢复 Graph 前持久化审批动作，防止响应丢失后重复投影。"""

    try:
        return services.require_conversation_repository().accept_action(
            thread_id=thread_id,
            subject_id=access.identity.subject_id,
            workspace_id=access.principal.workspace_id,
            access_mode=access.principal.mode,
            client_request_id=client_request_id,
            request_kind=request_kind,
            label=label,
            request_payload=payload,
        )
    except ConversationDataError as error:
        status = 404 if error.error_code.endswith("not_accessible") else 409
        raise api_error(status, error.error_code) from None


def _run_accepted_response(accepted: AcceptedRun) -> RunAcceptedResponse:
    """把内部幂等接受结果转换为不含摘要和 Checkpoint 的公开资源。"""

    return RunAcceptedResponse(
        run=PublicAgentRun.from_domain(accepted.run),
        user_message=accepted.message,
        reused=accepted.reused,
    )


def _existing_action(
    services: WebServices,
    *,
    thread_id: str,
    client_request_id: str,
) -> AcceptedRun | None:
    """读取已接受动作，供响应丢失后的相同决定直接复用。"""

    return services.require_conversation_repository().get_accepted_by_client_request(
        thread_id=thread_id,
        client_request_id=client_request_id,
    )


def _pending_snapshot(request: Request, thread_id: str):
    """使用无模型恢复依赖读取指定 thread 的当前 Checkpoint。"""

    services = get_services(request)
    with open_sqlite_checkpointer(services.settings.checkpoint_db_path) as checkpointer:
        graph = build_workflow(
            services.refund_resume_dependencies(),
            checkpointer=checkpointer,
        )
        return graph.get_state({"configurable": {"thread_id": thread_id}})


def _run_context(
    access: RequestAccess,
    thread_id: str,
    *,
    l2_allowed: bool,
    l2_quota_remaining: int,
) -> RunContext:
    """从可信 Session 构造不能由客户端覆盖的 Graph Runtime Context。"""

    return RunContext(
        user_id=access.principal.actor_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
        as_of=datetime.now(UTC).date(),
        task_id=thread_id,
        subject_id=access.identity.subject_id,
        l2_allowed=l2_allowed,
        l2_quota_remaining=l2_quota_remaining,
    )


def _invoke_registered_l2(
    request: Request,
    access: RequestAccess,
    thread_id: str,
    command: Command,
):
    """使用独立 Memory Store 和同一 Checkpoint 恢复一次 L2 待处理动作。"""

    services = get_services(request)
    dependencies, remaining = _l2_resume_dependencies(services, access)
    with open_sqlite_memory_store(services.settings.memory_db_path) as store:
        with open_sqlite_checkpointer(
            services.settings.checkpoint_db_path
        ) as checkpointer:
            graph = build_workflow(
                dependencies,
                checkpointer=checkpointer,
                store=store,
            )
            result = graph.invoke(
                command,
                config={"configurable": {"thread_id": thread_id}},
                context=_run_context(
                    access,
                    thread_id,
                    l2_allowed=True,
                    l2_quota_remaining=remaining,
                ),
            )
            snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    return result, snapshot.next


@router.post("/chat/messages", response_model=ChatResponse)
def send_chat_message(
    request: Request,
    payload: ChatMessageRequest,
) -> ChatResponse:
    """验证 thread 归属后执行一轮唯一主图并返回公开结果。"""

    services = get_services(request)
    access = require_mutation_access(request)
    enforce_rate_limit(
        services,
        f"chat:{access.principal.actor_id}",
        limit=60,
    )
    conversation = services.repository.get_authorized_conversation(
        thread_id=payload.thread_id,
        subject_id=access.identity.subject_id,
        workspace_id=access.principal.workspace_id,
        access_mode=access.principal.mode,
    )
    if conversation is None:
        raise api_error(404, "conversation_not_accessible")
    with services.thread_locks.acquire(payload.thread_id) as acquired:
        if not acquired:
            raise api_error(409, "thread_busy")
        snapshot = (
            _pending_snapshot(request, payload.thread_id)
            if access.principal.mode == "registered"
            else None
        )
        if snapshot is not None and snapshot.interrupts:
            if snapshot.next == ("l2_await_user_input",):
                result, next_nodes = _invoke_registered_l2(
                    request,
                    access,
                    payload.thread_id,
                    Command(resume={"message": payload.message}),
                )
                services.repository.touch_conversation(payload.thread_id)
                return _persist_legacy_exchange(
                    services,
                    access,
                    thread_id=payload.thread_id,
                    user_message=payload.message,
                    response=_chat_response(
                        services,
                        access,
                        payload.thread_id,
                        result,
                        next_nodes=next_nodes,
                    ),
                )
            error_codes = {
                ("l2_await_upgrade_confirmation",): ("l2_upgrade_decision_required"),
                ("l2_await_memory_confirmation",): ("l2_memory_decision_required"),
                ("await_refund_approval",): "refund_approval_required",
            }
            error_code = error_codes.get(snapshot.next)
            if error_code is not None:
                raise api_error(409, error_code)
        if access.principal.mode == "guest":
            dependencies = services.guest_dependencies(access.principal)
            remaining = 0
        else:
            dependencies, remaining = _registered_dependencies(services, access)
        try:
            with open_sqlite_memory_store(services.settings.memory_db_path) as store:
                with open_sqlite_checkpointer(
                    services.settings.checkpoint_db_path
                ) as checkpointer:
                    graph = build_workflow(
                        dependencies,
                        checkpointer=checkpointer,
                        store=store,
                    )
                    result = graph.invoke(
                        {"messages": [{"role": "user", "content": payload.message}]},
                        config={"configurable": {"thread_id": payload.thread_id}},
                        context=_run_context(
                            access,
                            payload.thread_id,
                            l2_allowed=access.principal.mode == "registered",
                            l2_quota_remaining=remaining,
                        ),
                    )
                    next_nodes = graph.get_state(
                        {"configurable": {"thread_id": payload.thread_id}}
                    ).next
        except InterpreterUnavailableError:
            raise api_error(503, "llm_temporarily_failed") from None
        except (LookupError, ValueError):
            raise api_error(422, "query_rejected") from None

    services.repository.touch_conversation(payload.thread_id)
    return _persist_legacy_exchange(
        services,
        access,
        thread_id=payload.thread_id,
        user_message=payload.message,
        response=_chat_response(
            services,
            access,
            payload.thread_id,
            result,
            next_nodes=next_nodes,
        ),
    )


@router.get(
    "/conversations/{thread_id}/pending-l2",
    response_model=PendingL2Response,
)
def get_pending_l2(thread_id: str, request: Request) -> PendingL2Response:
    """授权后返回当前 conversation 的有限 L2 待处理卡片。"""

    _authorized_conversation(request, thread_id, mutation=False)
    snapshot = _pending_snapshot(request, thread_id)
    action = _pending_action(snapshot.values, snapshot.next)
    if not snapshot.interrupts or action is None:
        return PendingL2Response(pending=False, public_status="none")
    preview = snapshot.values.get("l2_upgrade_preview")
    runtime = snapshot.values.get("l2_runtime")
    proposal = (
        runtime.pending_memory_proposal if isinstance(runtime, L2RuntimeState) else None
    )
    return PendingL2Response(
        pending=True,
        public_status=str(snapshot.values.get("status", "l2_active")),
        pending_action=action,
        upgrade_preview=(
            PublicL2UpgradePreview.from_domain(preview)
            if isinstance(preview, L2UpgradePreview)
            else None
        ),
        memory_proposal=(
            PublicMemoryProposal.from_domain(proposal) if proposal is not None else None
        ),
    )


@router.post(
    "/conversations/{thread_id}/l2-upgrade-decision",
    response_model=RunAcceptedResponse,
    status_code=202,
)
def decide_l2_upgrade(
    thread_id: str,
    request: Request,
    payload: L2UpgradeDecisionRequest,
    background_tasks: BackgroundTasks,
) -> RunAcceptedResponse:
    """验证预览绑定、接受决定，并在后台恢复升级流程。"""

    services = get_services(request)
    access, _ = _authorized_conversation(request, thread_id, mutation=True)
    client_request_id = _action_client_id(
        "l2-upgrade", payload.preview_id, payload.decision
    )
    existing = _existing_action(
        services,
        thread_id=thread_id,
        client_request_id=client_request_id,
    )
    if existing is not None:
        return _run_accepted_response(existing)
    snapshot = _pending_snapshot(request, thread_id)
    preview = snapshot.values.get("l2_upgrade_preview")
    if (
        not snapshot.interrupts
        or snapshot.next != ("l2_await_upgrade_confirmation",)
        or not isinstance(preview, L2UpgradePreview)
        or preview.preview_id != payload.preview_id
    ):
        raise api_error(404, "l2_pending_action_not_accessible")
    accepted = _accept_public_action(
        services,
        access,
        thread_id=thread_id,
        request_kind="l2_upgrade_decision",
        client_request_id=client_request_id,
        label=(
            "确认升级至 AI 二线客服" if payload.decision == "confirm" else "取消升级"
        ),
        payload={"preview_id": payload.preview_id, "decision": payload.decision},
    )
    if not accepted.reused:
        background_tasks.add_task(
            ConversationRuntime(services).execute_action_run,
            access,
            accepted,
            {"preview_id": payload.preview_id, "decision": payload.decision},
        )
    return _run_accepted_response(accepted)


@router.post(
    "/conversations/{thread_id}/l2-memory-decision",
    response_model=RunAcceptedResponse,
    status_code=202,
)
def decide_l2_memory(
    thread_id: str,
    request: Request,
    payload: L2MemoryDecisionRequest,
    background_tasks: BackgroundTasks,
) -> RunAcceptedResponse:
    """验证偏好建议绑定、接受决定，并在后台恢复 Memory 流程。"""

    services = get_services(request)
    access, _ = _authorized_conversation(request, thread_id, mutation=True)
    client_request_id = _action_client_id(
        "memory", payload.proposal_id, payload.decision
    )
    existing = _existing_action(
        services,
        thread_id=thread_id,
        client_request_id=client_request_id,
    )
    if existing is not None:
        return _run_accepted_response(existing)
    snapshot = _pending_snapshot(request, thread_id)
    runtime = snapshot.values.get("l2_runtime")
    proposal = (
        runtime.pending_memory_proposal if isinstance(runtime, L2RuntimeState) else None
    )
    if (
        not snapshot.interrupts
        or snapshot.next != ("l2_await_memory_confirmation",)
        or proposal is None
        or proposal.proposal_id != payload.proposal_id
    ):
        raise api_error(404, "l2_pending_action_not_accessible")
    accepted = _accept_public_action(
        services,
        access,
        thread_id=thread_id,
        request_kind="memory_decision",
        client_request_id=client_request_id,
        label=(
            "保存该长期偏好" if payload.decision == "confirm" else "不保存该长期偏好"
        ),
        payload={"proposal_id": payload.proposal_id, "decision": payload.decision},
    )
    if not accepted.reused:
        background_tasks.add_task(
            ConversationRuntime(services).execute_action_run,
            access,
            accepted,
            {"proposal_id": payload.proposal_id, "decision": payload.decision},
        )
    return _run_accepted_response(accepted)


@router.get(
    "/conversations/{thread_id}/pending-refund",
    response_model=PendingRefundResponse,
)
def get_pending_refund(thread_id: str, request: Request) -> PendingRefundResponse:
    """授权后返回当前 thread 可见且仍在等待决定的退款预览。"""

    access, _ = _authorized_conversation(request, thread_id, mutation=False)
    snapshot = _pending_snapshot(request, thread_id)
    preview = snapshot.values.get("refund_preview")
    if (
        preview is None
        or not snapshot.interrupts
        or snapshot.next != ("await_refund_approval",)
    ):
        return PendingRefundResponse(pending=False, public_status="none")
    action = (
        get_services(request)
        .require_refund_repository()
        .get_action(
            user_id=access.principal.actor_id,
            workspace_id=access.principal.workspace_id,
            task_id=thread_id,
            action_id=preview.action_id,
        )
    )
    if action is None or action.preview_hash != preview.preview_hash:
        raise api_error(404, "refund_action_not_accessible")
    if action.status != "awaiting_approval":
        return PendingRefundResponse(
            pending=False,
            public_status=(
                "refund_preview_stale"
                if action.status == "stale"
                else f"refund_{action.status}"
            ),
        )
    return PendingRefundResponse(
        pending=True,
        public_status="refund_awaiting_approval",
        refund_preview=PublicRefundPreview.from_domain(preview),
    )


@router.post(
    "/conversations/{thread_id}/refund-approval",
    response_model=RunAcceptedResponse,
    status_code=202,
)
def decide_refund(
    thread_id: str,
    request: Request,
    payload: RefundApprovalRequest,
    background_tasks: BackgroundTasks,
) -> RunAcceptedResponse:
    """验证绑定退款动作、接受决定，并在后台恢复同一 Graph task。"""

    services = get_services(request)
    access, _ = _authorized_conversation(request, thread_id, mutation=True)
    scope_user_id = access.principal.actor_id
    client_request_id = _action_client_id("refund", payload.action_id, payload.decision)
    action = services.require_refund_repository().get_action(
        user_id=scope_user_id,
        workspace_id=access.principal.workspace_id,
        task_id=thread_id,
        action_id=payload.action_id,
    )
    if action is None:
        raise api_error(404, "refund_action_not_accessible")
    if action.status == "stale":
        raise api_error(409, "refund_preview_stale")
    if action.status == "rejected":
        if payload.decision != "reject":
            raise api_error(409, "refund_action_closed")
        existing = _existing_action(
            services,
            thread_id=thread_id,
            client_request_id=client_request_id,
        )
        if existing is None:
            raise api_error(409, "refund_action_closed")
        return _run_accepted_response(existing)
    if action.status == "completed":
        if payload.decision != "approve":
            raise api_error(409, "refund_action_closed")
        existing = _existing_action(
            services,
            thread_id=thread_id,
            client_request_id=client_request_id,
        )
        if existing is None:
            raise api_error(409, "refund_action_closed")
        return _run_accepted_response(existing)
    if action.status != "awaiting_approval":
        raise api_error(409, "refund_action_closed")
    existing = _existing_action(
        services,
        thread_id=thread_id,
        client_request_id=client_request_id,
    )
    if existing is not None:
        return _run_accepted_response(existing)
    snapshot = _pending_snapshot(request, thread_id)
    preview = snapshot.values.get("refund_preview")
    if (
        not snapshot.interrupts
        or snapshot.next != ("await_refund_approval",)
        or preview is None
        or preview.action_id != payload.action_id
    ):
        raise api_error(404, "refund_action_not_accessible")
    accepted = _accept_public_action(
        services,
        access,
        thread_id=thread_id,
        request_kind="refund_decision",
        client_request_id=client_request_id,
        label=("批准 Mock 退款" if payload.decision == "approve" else "拒绝 Mock 退款"),
        payload={"action_id": payload.action_id, "decision": payload.decision},
    )
    if not accepted.reused:
        background_tasks.add_task(
            ConversationRuntime(services).execute_action_run,
            access,
            accepted,
            {"action_id": payload.action_id, "decision": payload.decision},
        )
    return _run_accepted_response(accepted)
