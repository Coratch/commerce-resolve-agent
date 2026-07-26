"""增加 v1.3 商品快照、演示场景和多包裹履约数据。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0008"
down_revision: str | Sequence[str] | None = "20260722_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """以可空字段兼容旧订单，并创建独立包裹及播种幂等表。"""

    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("demo_scenario_id", sa.String(length=80)))
        batch.add_column(sa.Column("catalog_version", sa.String(length=40)))
    op.create_index(
        "uq_orders_workspace_demo_scenario",
        "orders",
        ["workspace_id", "demo_scenario_id"],
        unique=True,
        sqlite_where=sa.text("demo_scenario_id IS NOT NULL"),
    )
    with op.batch_alter_table("order_items") as batch:
        batch.add_column(sa.Column("product_ref", sa.String(length=80)))
        batch.add_column(sa.Column("variant_title", sa.String(length=120)))
        batch.add_column(sa.Column("unit_amount_minor", sa.Integer()))
        batch.add_column(sa.Column("currency", sa.String(length=3)))
        batch.add_column(sa.Column("image_ref", sa.String(length=120)))
        batch.add_column(sa.Column("catalog_version", sa.String(length=40)))
        batch.create_check_constraint(
            "ck_order_items_snapshot_amount",
            "unit_amount_minor IS NULL OR unit_amount_minor >= 0",
        )
        batch.create_check_constraint(
            "ck_order_items_snapshot_currency",
            "currency IS NULL OR currency = 'CNY'",
        )

    op.create_table(
        "shipment_packages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("order_pk", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=64), nullable=False),
        sa.Column("carrier", sa.String(length=80)),
        sa.Column("tracking_number", sa.String(length=100)),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_event", sa.String(length=300), nullable=False),
        sa.Column("estimated_delivery_at", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('preparing', 'in_transit', 'delivered')",
            name="ck_shipment_packages_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["order_pk"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_pk",
            "package_id",
            name="uq_shipment_packages_order_package",
        ),
    )
    op.create_index(
        "ix_shipment_packages_workspace_id",
        "shipment_packages",
        ["workspace_id"],
    )
    op.create_index(
        "ix_shipment_packages_order_pk",
        "shipment_packages",
        ["order_pk"],
    )

    op.create_table(
        "shipment_package_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_pk", sa.String(length=36), nullable=False),
        sa.Column("order_item_pk", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99",
            name="ck_shipment_package_items_quantity",
        ),
        sa.ForeignKeyConstraint(
            ["package_pk"],
            ["shipment_packages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_pk"],
            ["order_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_pk",
            "order_item_pk",
            name="uq_shipment_package_items_package_item",
        ),
    )
    op.create_index(
        "ix_shipment_package_items_package_pk",
        "shipment_package_items",
        ["package_pk"],
    )
    op.create_index(
        "ix_shipment_package_items_order_item_pk",
        "shipment_package_items",
        ["order_item_pk"],
    )

    op.create_table(
        "demo_seed_requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("admin_user_id", sa.String(length=36), nullable=False),
        sa.Column("target_user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=80), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id"),
        sa.UniqueConstraint(
            "admin_user_id",
            "client_request_id",
            name="uq_demo_seed_requests_admin_client",
        ),
    )
    op.create_index(
        "ix_demo_seed_requests_admin_user_id",
        "demo_seed_requests",
        ["admin_user_id"],
    )
    op.create_index(
        "ix_demo_seed_requests_target_user_id",
        "demo_seed_requests",
        ["target_user_id"],
    )
    op.create_index(
        "ix_demo_seed_requests_workspace_id",
        "demo_seed_requests",
        ["workspace_id"],
    )


def downgrade() -> None:
    """仅供本地回退，移除 v1.3 新表和可空快照字段。"""

    op.drop_index(
        "ix_demo_seed_requests_workspace_id",
        table_name="demo_seed_requests",
    )
    op.drop_index(
        "ix_demo_seed_requests_target_user_id",
        table_name="demo_seed_requests",
    )
    op.drop_index(
        "ix_demo_seed_requests_admin_user_id",
        table_name="demo_seed_requests",
    )
    op.drop_table("demo_seed_requests")
    op.drop_index(
        "ix_shipment_package_items_order_item_pk",
        table_name="shipment_package_items",
    )
    op.drop_index(
        "ix_shipment_package_items_package_pk",
        table_name="shipment_package_items",
    )
    op.drop_table("shipment_package_items")
    op.drop_index(
        "ix_shipment_packages_order_pk",
        table_name="shipment_packages",
    )
    op.drop_index(
        "ix_shipment_packages_workspace_id",
        table_name="shipment_packages",
    )
    op.drop_table("shipment_packages")
    with op.batch_alter_table("order_items") as batch:
        batch.drop_constraint("ck_order_items_snapshot_currency", type_="check")
        batch.drop_constraint("ck_order_items_snapshot_amount", type_="check")
        batch.drop_column("catalog_version")
        batch.drop_column("image_ref")
        batch.drop_column("currency")
        batch.drop_column("unit_amount_minor")
        batch.drop_column("variant_title")
        batch.drop_column("product_ref")
    op.drop_index("uq_orders_workspace_demo_scenario", table_name="orders")
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("catalog_version")
        batch.drop_column("demo_scenario_id")
