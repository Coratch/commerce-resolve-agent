"""创建 v0.4 Mock 支付、退款动作、退款结果和审计表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0002"
down_revision: str | Sequence[str] | None = "20260717_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """在保留 v0.3 数据的前提下增加 Mock 退款业务表和约束。"""

    op.create_table(
        "mock_payments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("order_pk", sa.String(length=36), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_mock_payments_amount"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_mock_payments_currency"),
        sa.CheckConstraint(
            "channel IN ('mock_card', 'mock_wallet')",
            name="ck_mock_payments_channel",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'settled', 'failed', 'refunded')",
            name="ck_mock_payments_status",
        ),
        sa.ForeignKeyConstraint(["order_pk"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_pk"),
    )
    op.create_index(
        "ix_mock_payments_workspace_id",
        "mock_payments",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "refund_actions",
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("order_pk", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("reason_detail", sa.String(length=300), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("policy_version", sa.String(length=200), nullable=False),
        sa.Column("policy_fact_ids_json", sa.String(length=2000), nullable=False),
        sa.Column("facts_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("preview_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_refund_actions_amount"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_refund_actions_currency"),
        sa.CheckConstraint(
            "channel IN ('mock_card', 'mock_wallet')",
            name="ck_refund_actions_channel",
        ),
        sa.CheckConstraint(
            "reason_code IN ('no_longer_needed', 'quality_issue', "
            "'delivery_issue', 'other')",
            name="ck_refund_actions_reason",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'rejected', 'stale', 'executing', "
            "'completed', 'failed', 'unknown', 'verification_failed')",
            name="ck_refund_actions_status",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["conversations.thread_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["order_pk"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["mock_payments.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("task_id", "subject_id", "user_id", "workspace_id", "order_pk"):
        op.create_index(
            f"ix_refund_actions_{column}",
            "refund_actions",
            [column],
            unique=False,
        )
    op.create_index(
        "uq_refund_actions_active_order",
        "refund_actions",
        ["order_pk"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('awaiting_approval', 'executing', 'unknown', 'completed')"
        ),
    )

    op.create_table(
        "mock_refunds",
        sa.Column("refund_id", sa.String(length=48), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("order_pk", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("gateway_result_code", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_mock_refunds_amount"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_mock_refunds_currency"),
        sa.CheckConstraint(
            "channel IN ('mock_card', 'mock_wallet')",
            name="ck_mock_refunds_channel",
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed', 'unknown')",
            name="ck_mock_refunds_status",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["refund_actions.action_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["mock_payments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["order_pk"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("refund_id"),
        sa.UniqueConstraint("action_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_mock_refunds_workspace_id",
        "mock_refunds",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_mock_refunds_order_pk",
        "mock_refunds",
        ["order_pk"],
        unique=False,
    )
    op.create_index(
        "uq_mock_refunds_active_payment",
        "mock_refunds",
        ["payment_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('processing', 'succeeded', 'unknown')"),
    )

    op.create_table(
        "refund_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("result_code", sa.String(length=80), nullable=False),
        sa.Column("preview_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_id"], ["refund_actions.action_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "event_key", name="uq_refund_audit_event_key"),
    )
    op.create_index(
        "ix_refund_audit_events_action_id",
        "refund_audit_events",
        ["action_id"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖逆序删除 v0.4 表；仅允许在无保留审计需求的本地环境使用。"""

    op.drop_index("ix_refund_audit_events_action_id", table_name="refund_audit_events")
    op.drop_table("refund_audit_events")
    op.drop_index("uq_mock_refunds_active_payment", table_name="mock_refunds")
    op.drop_index("ix_mock_refunds_order_pk", table_name="mock_refunds")
    op.drop_index("ix_mock_refunds_workspace_id", table_name="mock_refunds")
    op.drop_table("mock_refunds")
    op.drop_index("uq_refund_actions_active_order", table_name="refund_actions")
    for column in ("order_pk", "workspace_id", "user_id", "subject_id", "task_id"):
        op.drop_index(f"ix_refund_actions_{column}", table_name="refund_actions")
    op.drop_table("refund_actions")
    op.drop_index("ix_mock_payments_workspace_id", table_name="mock_payments")
    op.drop_table("mock_payments")
