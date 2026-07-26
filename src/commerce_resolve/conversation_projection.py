"""把 LangGraph 内部 State 投影为稳定、有限的公开对话数据。"""

from __future__ import annotations

from typing import Any

from commerce_resolve.l2_models import L2RuntimeState, L2UpgradePreview
from commerce_resolve.service_resolution import ServiceResolution
from commerce_resolve.web.dependencies import RequestAccess, WebServices
from commerce_resolve.web.schemas import (
    ChatResponse,
    PublicL2CaseSummary,
    PublicL2TraceEvent,
    PublicL2UpgradePreview,
    PublicMemoryProposal,
    PublicRefundPreview,
    PublicRefundResult,
)


class ConversationProjectionError(ValueError):
    """表示内部 State 无法安全形成公开消息。"""


def pending_action(
    state: dict[str, object],
    next_nodes: tuple[str, ...] = (),
) -> str | None:
    """把内部中断节点或 L2 phase 映射为有限公开动作名称。"""

    node_actions = {
        ("l2_await_upgrade_confirmation",): "upgrade_confirmation",
        ("l2_await_user_input",): "user_input",
        ("l2_await_memory_confirmation",): "memory_confirmation",
        ("await_refund_approval",): "refund_approval",
    }
    if next_nodes in node_actions:
        return node_actions[next_nodes]
    runtime = state.get("l2_runtime")
    if isinstance(runtime, L2RuntimeState):
        return {
            "awaiting_confirmation": "upgrade_confirmation",
            "waiting_user": "user_input",
            "waiting_memory_confirmation": "memory_confirmation",
            "waiting_refund_approval": "refund_approval",
        }.get(runtime.phase)
    return None


def project_chat_response(
    services: WebServices,
    access: RequestAccess,
    thread_id: str,
    state: dict[str, object],
    *,
    next_nodes: tuple[str, ...] = (),
) -> ChatResponse:
    """只从白名单公开 State 构造消息、引用和动作卡片。"""

    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ConversationProjectionError("missing_public_message")
    assistant_message = messages[-1].content
    if not isinstance(assistant_message, str):
        raise ConversationProjectionError("invalid_public_message")
    preview = state.get("refund_preview")
    verification = state.get("refund_verification")
    l2_preview = state.get("l2_upgrade_preview")
    l2_runtime = state.get("l2_runtime")
    case_summary = None
    trace_events: tuple[PublicL2TraceEvent, ...] = ()
    memory_proposal = None
    if isinstance(l2_runtime, L2RuntimeState):
        if l2_runtime.pending_memory_proposal is not None:
            memory_proposal = PublicMemoryProposal.from_domain(
                l2_runtime.pending_memory_proposal
            )
        if l2_runtime.case_id is not None:
            case = services.require_l2_repository().get_authorized_case(
                case_id=l2_runtime.case_id,
                subject_id=access.identity.subject_id,
                user_id=access.principal.actor_id,
                workspace_id=access.principal.workspace_id,
                thread_id=thread_id,
            )
            if case is not None:
                case_summary = PublicL2CaseSummary.from_domain(case)
                trace_events = tuple(
                    PublicL2TraceEvent.from_domain(event)
                    for event in services.require_l2_repository().list_events(
                        case_id=case.case_id,
                        user_id=access.principal.actor_id,
                        workspace_id=access.principal.workspace_id,
                    )
                )
    action = pending_action(state, next_nodes)
    service_resolution = state.get("service_resolution")
    if service_resolution is not None and not isinstance(
        service_resolution,
        ServiceResolution,
    ):
        raise ConversationProjectionError("invalid_service_resolution")
    return ChatResponse(
        thread_id=thread_id,
        assistant_message=assistant_message,
        public_status=str(state.get("status", "completed")),
        citations=tuple(state.get("policy_citations", ())),
        refund_preview=(
            PublicRefundPreview.from_domain(preview)
            if action == "refund_approval" and preview is not None
            else None
        ),
        refund_result=(
            PublicRefundResult.from_domain(verification)
            if verification is not None
            else None
        ),
        l2_upgrade_preview=(
            PublicL2UpgradePreview.from_domain(l2_preview)
            if action == "upgrade_confirmation"
            and isinstance(l2_preview, L2UpgradePreview)
            else None
        ),
        l2_case_summary=case_summary,
        l2_pending_action=action,
        l2_trace_events=trace_events,
        memory_proposal=(memory_proposal if action == "memory_confirmation" else None),
        service_resolution=service_resolution,
    )


def public_message_payload(response: ChatResponse) -> dict[str, Any]:
    """把已校验响应转换为消息附属 Payload，不包含重复正文。"""

    payload = response.model_dump(mode="json", exclude_none=True)
    payload.pop("thread_id", None)
    payload.pop("assistant_message", None)
    return payload
