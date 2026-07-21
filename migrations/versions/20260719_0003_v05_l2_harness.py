"""创建 v0.5 L2 Case、公开轨迹和模型调用计量表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0003"
down_revision: str | Sequence[str] | None = "20260717_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """在保留 v0.4 业务数据的前提下增加 L2 Harness 业务表。"""

    op.create_table(
        "l2_support_cases",
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("related_order_id", sa.String(length=36), nullable=True),
        sa.Column("issue_summary", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stop_reason", sa.String(length=40), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("toolset_version", sa.String(length=40), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("max_active_milliseconds", sa.Integer(), nullable=False),
        sa.Column("max_invocation_milliseconds", sa.Integer(), nullable=False),
        sa.Column("max_consecutive_tool_failures", sa.Integer(), nullable=False),
        sa.Column("steps_used", sa.Integer(), nullable=False),
        sa.Column("model_calls_used", sa.Integer(), nullable=False),
        sa.Column("tool_calls_used", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens_used", sa.Integer(), nullable=False),
        sa.Column("active_milliseconds", sa.Integer(), nullable=False),
        sa.Column("consecutive_tool_failures", sa.Integer(), nullable=False),
        sa.Column("last_action_signature", sa.String(length=64), nullable=True),
        sa.Column("repeated_action_count", sa.Integer(), nullable=False),
        sa.Column("final_response", sa.String(length=1200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('l2_active', 'l2_waiting_user', 'l2_waiting_approval', "
            "'l2_resolved', 'l2_unresolved', 'l2_budget_exhausted', "
            "'l2_cancelled', 'l2_stopped')",
            name="ck_l2_cases_status",
        ),
        sa.CheckConstraint(
            "max_steps > 0 AND max_model_calls > 0 AND max_tool_calls > 0",
            name="ck_l2_cases_positive_limits",
        ),
        sa.CheckConstraint(
            "max_estimated_tokens > 0 AND max_active_milliseconds > 0",
            name="ck_l2_cases_positive_resource_limits",
        ),
        sa.CheckConstraint(
            "steps_used >= 0 AND model_calls_used >= 0 AND tool_calls_used >= 0 "
            "AND estimated_tokens_used >= 0 AND active_milliseconds >= 0",
            name="ck_l2_cases_nonnegative_usage",
        ),
        sa.CheckConstraint(
            "steps_used <= max_steps AND model_calls_used <= max_model_calls "
            "AND tool_calls_used <= max_tool_calls "
            "AND estimated_tokens_used <= max_estimated_tokens "
            "AND active_milliseconds <= max_active_milliseconds",
            name="ck_l2_cases_usage_within_limits",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["conversations.thread_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index(
        "ix_l2_support_cases_thread_id",
        "l2_support_cases",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        "ix_l2_support_cases_subject_id",
        "l2_support_cases",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_l2_support_cases_user_id",
        "l2_support_cases",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_l2_support_cases_workspace_id",
        "l2_support_cases",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_l2_cases_active_thread",
        "l2_support_cases",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('l2_active', 'l2_waiting_user', 'l2_waiting_approval')"
        ),
    )

    op.create_table(
        "l2_case_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=120), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("tool_category", sa.String(length=48), nullable=True),
        sa.Column("risk", sa.String(length=2), nullable=True),
        sa.Column("parameter_summary_json", sa.String(length=2000), nullable=True),
        sa.Column("result_code", sa.String(length=80), nullable=False),
        sa.Column("evidence_refs_json", sa.String(length=3000), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("step_number >= 0", name="ck_l2_case_events_step"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_l2_case_events_duration"),
        sa.CheckConstraint(
            "risk IS NULL OR risk IN ('R0', 'R1', 'R2')",
            name="ck_l2_case_events_risk",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["l2_support_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("case_id", "event_key", name="uq_l2_case_event_key"),
    )
    op.create_index(
        "ix_l2_case_events_case_id",
        "l2_case_events",
        ["case_id"],
        unique=False,
    )

    op.create_table(
        "llm_call_events",
        sa.Column("call_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("feature", sa.String(length=24), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("charged_tokens", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("feature = 'l2_agent'", name="ck_llm_call_events_feature"),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_llm_call_events_status",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 "
            "AND charged_tokens >= 0 AND duration_ms >= 0",
            name="ck_llm_call_events_usage",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["conversations.thread_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["l2_support_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("call_id"),
    )
    op.create_index(
        "ix_llm_call_events_user_id",
        "llm_call_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_call_events_usage_date",
        "llm_call_events",
        ["usage_date"],
        unique=False,
    )
    op.create_index(
        "ix_llm_call_events_thread_id",
        "llm_call_events",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_call_events_case_id",
        "llm_call_events",
        ["case_id"],
        unique=False,
    )


def downgrade() -> None:
    """仅用于本地开发，按依赖顺序删除 v0.5 新增表。"""

    op.drop_index("ix_llm_call_events_case_id", table_name="llm_call_events")
    op.drop_index("ix_llm_call_events_thread_id", table_name="llm_call_events")
    op.drop_index("ix_llm_call_events_usage_date", table_name="llm_call_events")
    op.drop_index("ix_llm_call_events_user_id", table_name="llm_call_events")
    op.drop_table("llm_call_events")
    op.drop_index("ix_l2_case_events_case_id", table_name="l2_case_events")
    op.drop_table("l2_case_events")
    op.drop_index("uq_l2_cases_active_thread", table_name="l2_support_cases")
    op.drop_index("ix_l2_support_cases_workspace_id", table_name="l2_support_cases")
    op.drop_index("ix_l2_support_cases_user_id", table_name="l2_support_cases")
    op.drop_index("ix_l2_support_cases_subject_id", table_name="l2_support_cases")
    op.drop_index("ix_l2_support_cases_thread_id", table_name="l2_support_cases")
    op.drop_table("l2_support_cases")
