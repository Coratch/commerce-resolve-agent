"""定义 v0.6 公开会话、消息、Run 与事件的领域契约。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ConversationLifecycle = Literal["active", "archived", "deleting", "deleted"]
HistoryState = Literal["complete", "partial"]
MessageRole = Literal["user", "assistant"]
MessageKind = Literal["text", "action", "status"]
MessageStatus = Literal["accepted", "completed", "failed"]
RunKind = Literal[
    "chat_message",
    "refund_decision",
    "l2_upgrade_decision",
    "memory_decision",
    "retry",
]
RunStatus = Literal[
    "accepted",
    "running",
    "waiting_action",
    "completed",
    "failed",
    "interrupted",
]
RunEventType = Literal[
    "run.accepted",
    "run.started",
    "step.updated",
    "action.required",
    "message.completed",
    "run.completed",
    "run.failed",
    "run.interrupted",
]


class ConversationSummary(BaseModel):
    """表示会话列表和详情共同使用的公开生命周期摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str
    related_order_id: str | None = None
    title: str
    lifecycle_status: ConversationLifecycle
    history_state: HistoryState
    message_count: int
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    pending_action: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ConversationMessage(BaseModel):
    """表示可向当前会话所有者公开的一条持久消息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    thread_id: str
    run_id: str | None = None
    sequence_no: int
    role: MessageRole
    kind: MessageKind
    content: str
    status: MessageStatus
    payload_version: int = 1
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AgentRun(BaseModel):
    """表示一条可查询、可恢复且具有明确终态的 Agent 执行。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    thread_id: str
    client_request_id: str
    request_kind: RunKind
    request_hash: str
    retry_of_run_id: str | None = None
    status: RunStatus
    pending_action: str | None = None
    checkpoint_id: str | None = None
    public_error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class AgentRunEvent(BaseModel):
    """表示可按递增 ID 重放且不包含隐藏推理的公开 Run 事件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: int
    run_id: str
    event_key: str
    event_type: RunEventType
    payload_version: int = 1
    payload: dict[str, Any]
    created_at: datetime


class AcceptedRun(BaseModel):
    """返回消息接受事务创建或幂等复用的 Run 与用户消息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: AgentRun
    message: ConversationMessage
    reused: bool = False
