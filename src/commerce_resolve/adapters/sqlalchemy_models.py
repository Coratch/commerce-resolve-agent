"""定义 v0.3 业务数据库的 SQLAlchemy 2.0 表映射。"""

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间，供持久记录默认值使用。"""

    return datetime.now(UTC)


class Base(DeclarativeBase):
    """作为 v0.3 业务表声明的统一元数据根。"""


class UserRow(Base):
    """保存注册账号及不可逆密码 Hash。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username_normalized: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkspaceRow(Base):
    """保存注册用户与唯一私有工作区的绑定。"""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class InvitationRow(Base):
    """保存邀请码摘要、有效期和原子使用计数。"""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("max_uses > 0", name="ck_invitations_max_uses"),
        CheckConstraint("used_count >= 0", name="ck_invitations_used_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WebSessionRow(Base):
    """保存可撤销浏览器 Session 和 CSRF Token 的摘要。"""

    __tablename__ = "web_sessions"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('guest', 'registered')",
            name="ck_web_sessions_actor_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationRow(Base):
    """保存 thread 与身份、工作区及访问模式的授权绑定。"""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "access_mode IN ('guest', 'registered')",
            name="ck_conversations_access_mode",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived', 'deleting', 'deleted')",
            name="ck_conversations_lifecycle_status",
        ),
        CheckConstraint(
            "history_state IN ('complete', 'partial')",
            name="ck_conversations_history_state",
        ),
        CheckConstraint(
            "message_count >= 0 AND next_message_sequence > 0",
            name="ck_conversations_message_count",
        ),
    )

    thread_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    access_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="新会话")
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )
    history_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="complete"
    )
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_message_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    last_message_preview: Mapped[str | None] = mapped_column(String(240))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_action: Mapped[str | None] = mapped_column(String(32))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AgentRunRow(Base):
    """保存一次幂等 Agent 请求的生命周期和公开失败状态。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "client_request_id", name="uq_agent_run_client_request"
        ),
        CheckConstraint(
            "request_kind IN ('chat_message', 'refund_decision', "
            "'l2_upgrade_decision', 'memory_decision', 'retry')",
            name="ck_agent_runs_request_kind",
        ),
        CheckConstraint(
            "status IN ('accepted', 'running', 'waiting_action', 'completed', "
            "'failed', 'interrupted')",
            name="ck_agent_runs_status",
        ),
        Index(
            "uq_agent_runs_active_thread",
            "thread_id",
            unique=True,
            sqlite_where=text("status IN ('accepted', 'running')"),
        ),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    pending_action: Mapped[str | None] = mapped_column(String(32))
    checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    public_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ConversationMessageRow(Base):
    """保存经公开投影过滤后的用户或助手消息。"""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "sequence_no", name="uq_conversation_message_sequence"
        ),
        CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_conversation_messages_role"
        ),
        CheckConstraint(
            "kind IN ('text', 'action', 'status')",
            name="ck_conversation_messages_kind",
        ),
        CheckConstraint(
            "status IN ('accepted', 'completed', 'failed')",
            name="ck_conversation_messages_status",
        ),
    )

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="SET NULL"), index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AgentRunEventRow(Base):
    """保存可按单调 ID 重放的脱敏 Agent Run 事件。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "event_key", name="uq_agent_run_event_key"),
        CheckConstraint(
            "event_type IN ('run.accepted', 'run.started', 'step.updated', "
            "'action.required', 'message.completed', 'run.completed', "
            "'run.failed', 'run.interrupted')",
            name="ck_agent_run_events_type",
        ),
    )

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class OrderRow(Base):
    """保存私有工作区中的最小演示订单事实。"""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("workspace_id", "order_id", name="uq_orders_workspace_order"),
        CheckConstraint(
            "status IN ('processing', 'shipped', 'delivered', 'cancelled')",
            name="ck_orders_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ShipmentRow(Base):
    """保存与同工作区订单一一对应的物流事实。"""

    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('preparing', 'in_transit', 'delivered')",
            name="ck_shipments_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_pk: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_event: Mapped[str] = mapped_column(String(300), nullable=False)
    estimated_delivery_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class MockPaymentRow(Base):
    """保存订单对应的一笔原始 Mock 支付事实。"""

    __tablename__ = "mock_payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_mock_payments_amount"),
        CheckConstraint("currency = 'CNY'", name="ck_mock_payments_currency"),
        CheckConstraint(
            "channel IN ('mock_card', 'mock_wallet')",
            name="ck_mock_payments_channel",
        ),
        CheckConstraint(
            "status IN ('pending', 'settled', 'failed', 'refunded')",
            name="ck_mock_payments_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_pk: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RefundActionRow(Base):
    """保存退款预览与审批绑定，不把待审批动作冒充退款结果。"""

    __tablename__ = "refund_actions"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_refund_actions_amount"),
        CheckConstraint("currency = 'CNY'", name="ck_refund_actions_currency"),
        CheckConstraint(
            "channel IN ('mock_card', 'mock_wallet')",
            name="ck_refund_actions_channel",
        ),
        CheckConstraint(
            "reason_code IN ('no_longer_needed', 'quality_issue', "
            "'delivery_issue', 'other')",
            name="ck_refund_actions_reason",
        ),
        CheckConstraint(
            "status IN ('awaiting_approval', 'rejected', 'stale', 'executing', "
            "'completed', 'failed', 'unknown', 'verification_failed')",
            name="ck_refund_actions_status",
        ),
        Index(
            "uq_refund_actions_active_order",
            "order_pk",
            unique=True,
            sqlite_where=text(
                "status IN ('awaiting_approval', 'executing', 'unknown', 'completed')"
            ),
        ),
    )

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.thread_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_pk: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("mock_payments.id", ondelete="RESTRICT"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_detail: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_fact_ids_json: Mapped[str] = mapped_column(String(2000), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class MockRefundRow(Base):
    """保存通过 Fake Refund Gateway 产生的本地退款业务事实。"""

    __tablename__ = "mock_refunds"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_mock_refunds_amount"),
        CheckConstraint("currency = 'CNY'", name="ck_mock_refunds_currency"),
        CheckConstraint(
            "channel IN ('mock_card', 'mock_wallet')",
            name="ck_mock_refunds_channel",
        ),
        CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed', 'unknown')",
            name="ck_mock_refunds_status",
        ),
        Index(
            "uq_mock_refunds_active_payment",
            "payment_id",
            unique=True,
            sqlite_where=text("status IN ('processing', 'succeeded', 'unknown')"),
        ),
    )

    refund_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("refund_actions.action_id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("mock_payments.id", ondelete="RESTRICT"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_pk: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    gateway_result_code: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RefundAuditEventRow(Base):
    """保存退款动作中不可变且不参与业务判断的脱敏审计事件。"""

    __tablename__ = "refund_audit_events"
    __table_args__ = (
        UniqueConstraint("action_id", "event_key", name="uq_refund_audit_event_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("refund_actions.action_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result_code: Mapped[str] = mapped_column(String(80), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LlmDailyUsageRow(Base):
    """保存注册用户按 UTC 日期计算的模型调用配额。"""

    __tablename__ = "llm_daily_usage"
    __table_args__ = (
        CheckConstraint("accepted_calls >= 0", name="ck_llm_usage_calls"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    accepted_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class L2SupportCaseRow(Base):
    """保存 L2 Support Case 的公开状态、预算快照和最终结果。"""

    __tablename__ = "l2_support_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('l2_active', 'l2_waiting_user', 'l2_waiting_approval', "
            "'l2_resolved', 'l2_unresolved', 'l2_budget_exhausted', "
            "'l2_cancelled', 'l2_stopped')",
            name="ck_l2_cases_status",
        ),
        CheckConstraint(
            "max_steps > 0 AND max_model_calls > 0 AND max_tool_calls > 0",
            name="ck_l2_cases_positive_limits",
        ),
        CheckConstraint(
            "max_estimated_tokens > 0 AND max_active_milliseconds > 0",
            name="ck_l2_cases_positive_resource_limits",
        ),
        CheckConstraint(
            "steps_used >= 0 AND model_calls_used >= 0 AND tool_calls_used >= 0 "
            "AND estimated_tokens_used >= 0 AND active_milliseconds >= 0",
            name="ck_l2_cases_nonnegative_usage",
        ),
        CheckConstraint(
            "steps_used <= max_steps AND model_calls_used <= max_model_calls "
            "AND tool_calls_used <= max_tool_calls "
            "AND estimated_tokens_used <= max_estimated_tokens "
            "AND active_milliseconds <= max_active_milliseconds",
            name="ck_l2_cases_usage_within_limits",
        ),
        CheckConstraint(
            "trace_state IN ('complete', 'partial', 'unavailable')",
            name="ck_l2_cases_trace_state",
        ),
        CheckConstraint(
            "next_event_sequence > 0",
            name="ck_l2_cases_event_sequence",
        ),
        Index(
            "uq_l2_cases_active_thread",
            "thread_id",
            unique=True,
            sqlite_where=text(
                "status IN ('l2_active', 'l2_waiting_user', 'l2_waiting_approval')"
            ),
        ),
    )

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.thread_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    related_order_id: Mapped[str | None] = mapped_column(String(36))
    issue_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    toolset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    context_policy_version: Mapped[str | None] = mapped_column(String(40))
    trace_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="partial"
    )
    failure_attribution: Mapped[str | None] = mapped_column(String(40))
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_model_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_active_milliseconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_invocation_milliseconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_consecutive_tool_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    active_milliseconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_tool_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_action_signature: Mapped[str | None] = mapped_column(String(64))
    repeated_action_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    final_response: Mapped[str | None] = mapped_column(String(1200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class L2CaseEventRow(Base):
    """保存不包含隐藏推理的幂等 L2 公开轨迹事件。"""

    __tablename__ = "l2_case_events"
    __table_args__ = (
        UniqueConstraint("case_id", "event_key", name="uq_l2_case_event_key"),
        UniqueConstraint("case_id", "sequence_no", name="uq_l2_case_event_sequence"),
        CheckConstraint("step_number >= 0", name="ck_l2_case_events_step"),
        CheckConstraint(
            "sequence_no > 0 AND payload_version > 0",
            name="ck_l2_case_events_sequence",
        ),
        CheckConstraint("duration_ms >= 0", name="ck_l2_case_events_duration"),
        CheckConstraint(
            "risk IS NULL OR risk IN ('R0', 'R1', 'R2')",
            name="ck_l2_case_events_risk",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("l2_support_cases.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    tool_category: Mapped[str | None] = mapped_column(String(48))
    risk: Mapped[str | None] = mapped_column(String(2))
    parameter_summary_json: Mapped[str | None] = mapped_column(String(2000))
    result_code: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_refs_json: Mapped[str] = mapped_column(
        String(3000), nullable=False, default="[]"
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_summary_json: Mapped[str | None] = mapped_column(String(4096))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class L2ContextManifestRow(Base):
    """保存不含消息正文的 L2 Context 选择与裁剪诊断元数据。"""

    __tablename__ = "l2_context_manifests"
    __table_args__ = (
        UniqueConstraint("case_id", "step_id", name="uq_l2_manifest_case_step"),
        CheckConstraint(
            "schema_version > 0 AND candidate_count >= 0 AND selected_count >= 0 "
            "AND duplicate_count >= 0 AND irrelevant_count >= 0 "
            "AND stale_count >= 0 AND conflict_count >= 0 "
            "AND out_of_scope_count >= 0 AND truncated_count >= 0 "
            "AND refresh_count >= 0",
            name="ck_l2_manifest_counts",
        ),
        CheckConstraint(
            "candidate_estimated_tokens >= 0 AND selected_estimated_tokens >= 0 "
            "AND pack_estimated_input_tokens >= 0 AND input_budget_tokens >= 0 "
            "AND reduction_basis_points BETWEEN 0 AND 10000 "
            "AND context_preparation_ms >= 0",
            name="ck_l2_manifest_metrics",
        ),
    )

    manifest_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("l2_support_cases.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    context_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    essential_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    irrelevant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    out_of_scope_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    pack_estimated_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    input_budget_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reduction_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_attribution: Mapped[str | None] = mapped_column(String(40))
    public_summary_json: Mapped[str] = mapped_column(String(4096), nullable=False)
    diagnostic_items_json: Mapped[str] = mapped_column(Text, nullable=False)
    context_preparation_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LlmCallEventRow(Base):
    """记录每次真实 L2 Provider 尝试及其 Token、耗时与结果。"""

    __tablename__ = "llm_call_events"
    __table_args__ = (
        CheckConstraint("feature = 'l2_agent'", name="ck_llm_call_events_feature"),
        CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_llm_call_events_status",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 "
            "AND charged_tokens >= 0 AND duration_ms >= 0",
            name="ck_llm_call_events_usage",
        ),
        CheckConstraint(
            "usage_source IN ('provider', 'estimated', 'unknown')",
            name="ck_llm_call_events_usage_source",
        ),
    )

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    feature: Mapped[str] = mapped_column(String(24), nullable=False)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.thread_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("l2_support_cases.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    manifest_id: Mapped[str | None] = mapped_column(
        ForeignKey("l2_context_manifests.manifest_id", ondelete="SET NULL"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    charged_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
