"""增加 v0.6 公开会话、消息、Run 与可重放事件结构。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0004"
down_revision: str | Sequence[str] | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保留既有业务数据，并为公开会话生命周期增加增量 Schema。"""

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(
            sa.Column(
                "title",
                sa.String(length=120),
                nullable=False,
                server_default="新会话",
            )
        )
        batch.add_column(
            sa.Column(
                "lifecycle_status",
                sa.String(length=16),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(
            sa.Column(
                "history_state",
                sa.String(length=16),
                nullable=False,
                server_default="partial",
            )
        )
        batch.add_column(
            sa.Column(
                "message_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "next_message_sequence",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(sa.Column("last_message_preview", sa.String(length=240)))
        batch.add_column(sa.Column("last_message_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("pending_action", sa.String(length=32)))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_conversations_lifecycle_status",
            "lifecycle_status IN ('active', 'archived', 'deleting', 'deleted')",
        )
        batch.create_check_constraint(
            "ck_conversations_history_state",
            "history_state IN ('complete', 'partial')",
        )
        batch.create_check_constraint(
            "ck_conversations_message_count",
            "message_count >= 0 AND next_message_sequence > 0",
        )

    op.create_index(
        "ix_conversations_subject_lifecycle_updated",
        "conversations",
        ["subject_id", "lifecycle_status", "updated_at", "thread_id"],
        unique=False,
    )

    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("request_kind", sa.String(length=32), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("retry_of_run_id", sa.String(length=36)),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("pending_action", sa.String(length=32)),
        sa.Column("checkpoint_id", sa.String(length=128)),
        sa.Column("public_error_code", sa.String(length=80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "request_kind IN ('chat_message', 'refund_decision', "
            "'l2_upgrade_decision', 'memory_decision', 'retry')",
            name="ck_agent_runs_request_kind",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'running', 'waiting_action', 'completed', "
            "'failed', 'interrupted')",
            name="ck_agent_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["conversations.thread_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_run_id"], ["agent_runs.run_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint(
            "thread_id", "client_request_id", name="uq_agent_run_client_request"
        ),
    )
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])
    op.create_index(
        "uq_agent_runs_active_thread",
        "agent_runs",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('accepted', 'running')"),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36)),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_conversation_messages_role"
        ),
        sa.CheckConstraint(
            "kind IN ('text', 'action', 'status')",
            name="ck_conversation_messages_kind",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'completed', 'failed')",
            name="ck_conversation_messages_status",
        ),
        sa.CheckConstraint(
            "sequence_no > 0 AND payload_version > 0",
            name="ck_conversation_messages_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["conversations.thread_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint(
            "thread_id", "sequence_no", name="uq_conversation_message_sequence"
        ),
    )
    op.create_index(
        "ix_conversation_messages_thread_sequence",
        "conversation_messages",
        ["thread_id", "sequence_no"],
    )
    op.create_index(
        "ix_conversation_messages_run_id",
        "conversation_messages",
        ["run_id"],
    )

    op.create_table(
        "agent_run_events",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('run.accepted', 'run.started', 'step.updated', "
            "'action.required', 'message.completed', 'run.completed', "
            "'run.failed', 'run.interrupted')",
            name="ck_agent_run_events_type",
        ),
        sa.CheckConstraint(
            "payload_version > 0", name="ck_agent_run_events_payload_version"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "event_key", name="uq_agent_run_event_key"),
    )
    op.create_index(
        "ix_agent_run_events_run_event",
        "agent_run_events",
        ["run_id", "event_id"],
    )


def downgrade() -> None:
    """仅用于本地开发，删除公开交互数据并恢复 v0.5 conversations。"""

    op.drop_index("ix_agent_run_events_run_event", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_conversation_messages_run_id", table_name="conversation_messages")
    op.drop_index(
        "ix_conversation_messages_thread_sequence",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")
    op.drop_index("uq_agent_runs_active_thread", table_name="agent_runs")
    op.drop_index("ix_agent_runs_thread_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(
        "ix_conversations_subject_lifecycle_updated", table_name="conversations"
    )
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("ck_conversations_message_count", type_="check")
        batch.drop_constraint("ck_conversations_history_state", type_="check")
        batch.drop_constraint("ck_conversations_lifecycle_status", type_="check")
        batch.drop_column("deleted_at")
        batch.drop_column("archived_at")
        batch.drop_column("pending_action")
        batch.drop_column("last_message_at")
        batch.drop_column("last_message_preview")
        batch.drop_column("next_message_sequence")
        batch.drop_column("message_count")
        batch.drop_column("history_state")
        batch.drop_column("lifecycle_status")
        batch.drop_column("title")
