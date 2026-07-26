"""增加 v2.0 版本化演示工作区、重置审计与单订单活动任务约束。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0009"
down_revision: str | Sequence[str] | None = "20260722_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """兼容增加工作区版本字段，并归档不符合 v2.0 边界的活动会话。"""

    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("dataset_version", sa.String(length=40)))
        batch.add_column(sa.Column("dataset_status", sa.String(length=16)))
        batch.add_column(
            sa.Column(
                "reset_generation",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("active_reset_request_id", sa.String(length=64)))
        batch.add_column(sa.Column("initialized_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_workspaces_dataset_status",
            "dataset_status IS NULL OR dataset_status IN "
            "('initializing', 'ready', 'resetting', 'failed')",
        )
        batch.create_check_constraint(
            "ck_workspaces_reset_generation",
            "reset_generation >= 0",
        )

    op.create_table(
        "workspace_reset_audit",
        sa.Column("reset_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.String(length=40), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_role IN ('customer', 'admin')",
            name="ck_workspace_reset_actor_role",
        ),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')",
            name="ck_workspace_reset_result",
        ),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_workspace_reset_generation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("reset_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "client_request_id",
            name="uq_workspace_reset_request",
        ),
    )
    op.create_index(
        "ix_workspace_reset_audit_workspace_id",
        "workspace_reset_audit",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_reset_audit_actor_user_id",
        "workspace_reset_audit",
        ["actor_user_id"],
    )

    op.execute(
        """
        UPDATE conversations
        SET lifecycle_status = 'archived',
            archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP)
        WHERE access_mode = 'registered'
          AND lifecycle_status = 'active'
          AND related_order_id IS NULL
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT thread_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY workspace_id, related_order_id
                       ORDER BY updated_at DESC, thread_id DESC
                   ) AS position
            FROM conversations
            WHERE access_mode = 'registered'
              AND lifecycle_status = 'active'
              AND related_order_id IS NOT NULL
        )
        UPDATE conversations
        SET lifecycle_status = 'archived',
            archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP)
        WHERE thread_id IN (
            SELECT thread_id FROM ranked WHERE position > 1
        )
        """
    )
    op.create_index(
        "uq_conversations_active_order_task",
        "conversations",
        ["workspace_id", "related_order_id"],
        unique=True,
        sqlite_where=sa.text(
            "access_mode = 'registered' "
            "AND lifecycle_status = 'active' "
            "AND related_order_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """仅供本地回退，移除 v2.0 工作区和活动任务约束。"""

    op.drop_index(
        "uq_conversations_active_order_task",
        table_name="conversations",
    )
    op.drop_index(
        "ix_workspace_reset_audit_actor_user_id",
        table_name="workspace_reset_audit",
    )
    op.drop_index(
        "ix_workspace_reset_audit_workspace_id",
        table_name="workspace_reset_audit",
    )
    op.drop_table("workspace_reset_audit")
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_constraint("ck_workspaces_reset_generation", type_="check")
        batch.drop_constraint("ck_workspaces_dataset_status", type_="check")
        batch.drop_column("initialized_at")
        batch.drop_column("active_reset_request_id")
        batch.drop_column("reset_generation")
        batch.drop_column("dataset_status")
        batch.drop_column("dataset_version")
