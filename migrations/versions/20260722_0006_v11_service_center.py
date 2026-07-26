"""增加 v1.1 订单商品行与 Conversation 可信订单绑定。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0006"
down_revision: str | Sequence[str] | None = "20260721_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保留 v1.0 数据，并增加可选商品行和空订单绑定字段。"""

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_pk", sa.String(length=36), nullable=False),
        sa.Column("sku", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("product_category", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99",
            name="ck_order_items_quantity",
        ),
        sa.CheckConstraint(
            "product_category IN ('general', 'apparel', 'hygiene', 'digital')",
            name="ck_order_items_product_category",
        ),
        sa.ForeignKeyConstraint(["order_pk"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_pk", "sku", name="uq_order_items_order_sku"),
    )
    op.create_index("ix_order_items_order_pk", "order_items", ["order_pk"])
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("related_order_id", sa.String(length=36)))
    op.create_index(
        "ix_conversations_workspace_related_order",
        "conversations",
        ["workspace_id", "related_order_id"],
    )


def downgrade() -> None:
    """仅供本地开发回退，移除 v1.1 增量字段与商品行。"""

    op.drop_index(
        "ix_conversations_workspace_related_order",
        table_name="conversations",
    )
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("related_order_id")
    op.drop_index("ix_order_items_order_pk", table_name="order_items")
    op.drop_table("order_items")
