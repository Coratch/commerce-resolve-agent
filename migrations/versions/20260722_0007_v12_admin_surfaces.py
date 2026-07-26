"""增加 v1.2 管理员角色与后台写操作审计。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0007"
down_revision: str | Sequence[str] | None = "20260722_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """把现有账号保留为客户，并创建独立后台写审计表。"""

    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "role",
                sa.String(length=16),
                nullable=False,
                server_default="customer",
            )
        )
        batch.create_check_constraint(
            "ck_users_role",
            "role IN ('customer', 'admin')",
        )
    op.create_table(
        "admin_action_audit",
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("admin_user_id", sa.String(length=36), nullable=False),
        sa.Column("target_user_id", sa.String(length=36)),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64)),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column(
            "parameter_summary_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')",
            name="ck_admin_action_audit_result",
        ),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        "ix_admin_action_audit_admin_user_id",
        "admin_action_audit",
        ["admin_user_id"],
    )
    op.create_index(
        "ix_admin_action_audit_target_user_id",
        "admin_action_audit",
        ["target_user_id"],
    )
    op.create_index(
        "ix_admin_action_audit_created_at",
        "admin_action_audit",
        ["created_at"],
    )


def downgrade() -> None:
    """仅供本地回退，移除后台审计与账号角色字段。"""

    op.drop_index("ix_admin_action_audit_created_at", table_name="admin_action_audit")
    op.drop_index(
        "ix_admin_action_audit_target_user_id", table_name="admin_action_audit"
    )
    op.drop_index(
        "ix_admin_action_audit_admin_user_id", table_name="admin_action_audit"
    )
    op.drop_table("admin_action_audit")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_column("role")
