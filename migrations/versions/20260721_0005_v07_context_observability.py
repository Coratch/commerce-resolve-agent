"""增加 v0.7 Context Manifest、稳定 Trace 序号和模型计量关联。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0005"
down_revision: str | Sequence[str] | None = "20260721_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_event_sequences() -> None:
    """按旧版稳定排序为每个 Case 回填单调事件序号。"""

    connection = op.get_bind()
    case_rows = connection.execute(
        sa.text("SELECT case_id FROM l2_support_cases ORDER BY case_id")
    ).mappings()
    for case_row in case_rows:
        event_rows = connection.execute(
            sa.text(
                "SELECT event_id FROM l2_case_events "
                "WHERE case_id = :case_id "
                "ORDER BY step_number, created_at, event_id"
            ),
            {"case_id": case_row["case_id"]},
        ).mappings()
        next_sequence = 1
        for event_row in event_rows:
            connection.execute(
                sa.text(
                    "UPDATE l2_case_events SET sequence_no = :sequence_no "
                    "WHERE event_id = :event_id"
                ),
                {
                    "sequence_no": next_sequence,
                    "event_id": event_row["event_id"],
                },
            )
            next_sequence += 1
        connection.execute(
            sa.text(
                "UPDATE l2_support_cases SET next_event_sequence = :next_sequence "
                "WHERE case_id = :case_id"
            ),
            {
                "next_sequence": next_sequence,
                "case_id": case_row["case_id"],
            },
        )


def upgrade() -> None:
    """原地保留 v0.6 数据，并加入可解释上下文和只读回放结构。"""

    with op.batch_alter_table("l2_support_cases") as batch:
        batch.add_column(sa.Column("context_policy_version", sa.String(length=40)))
        batch.add_column(
            sa.Column(
                "trace_state",
                sa.String(length=16),
                nullable=False,
                server_default="partial",
            )
        )
        batch.add_column(sa.Column("failure_attribution", sa.String(length=40)))
        batch.add_column(
            sa.Column(
                "next_event_sequence",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.create_check_constraint(
            "ck_l2_cases_trace_state",
            "trace_state IN ('complete', 'partial', 'unavailable')",
        )
        batch.create_check_constraint(
            "ck_l2_cases_event_sequence",
            "next_event_sequence > 0",
        )

    with op.batch_alter_table("l2_case_events") as batch:
        batch.add_column(sa.Column("sequence_no", sa.Integer()))
        batch.add_column(
            sa.Column(
                "payload_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(sa.Column("context_summary_json", sa.String(length=4096)))

    _backfill_event_sequences()

    with op.batch_alter_table("l2_case_events") as batch:
        batch.alter_column("sequence_no", nullable=False)
        batch.create_unique_constraint(
            "uq_l2_case_event_sequence", ["case_id", "sequence_no"]
        )
        batch.create_check_constraint(
            "ck_l2_case_events_sequence",
            "sequence_no > 0 AND payload_version > 0",
        )

    op.create_table(
        "l2_context_manifests",
        sa.Column("manifest_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("context_policy_version", sa.String(length=40), nullable=False),
        sa.Column("scope_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pack_hash", sa.String(length=64), nullable=False),
        sa.Column("essential_complete", sa.Boolean(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("irrelevant_count", sa.Integer(), nullable=False),
        sa.Column("stale_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("out_of_scope_count", sa.Integer(), nullable=False),
        sa.Column("truncated_count", sa.Integer(), nullable=False),
        sa.Column("refresh_count", sa.Integer(), nullable=False),
        sa.Column("candidate_estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("selected_estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("pack_estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("input_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("reduction_basis_points", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("failure_attribution", sa.String(length=40)),
        sa.Column("public_summary_json", sa.String(length=4096), nullable=False),
        sa.Column("diagnostic_items_json", sa.Text(), nullable=False),
        sa.Column("context_preparation_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version > 0 AND candidate_count >= 0 AND selected_count >= 0 "
            "AND duplicate_count >= 0 AND irrelevant_count >= 0 "
            "AND stale_count >= 0 AND conflict_count >= 0 "
            "AND out_of_scope_count >= 0 AND truncated_count >= 0 "
            "AND refresh_count >= 0",
            name="ck_l2_manifest_counts",
        ),
        sa.CheckConstraint(
            "candidate_estimated_tokens >= 0 AND selected_estimated_tokens >= 0 "
            "AND pack_estimated_input_tokens >= 0 AND input_budget_tokens >= 0 "
            "AND reduction_basis_points BETWEEN 0 AND 10000 "
            "AND context_preparation_ms >= 0",
            name="ck_l2_manifest_metrics",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["l2_support_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("manifest_id"),
        sa.UniqueConstraint("case_id", "step_id", name="uq_l2_manifest_case_step"),
    )
    op.create_index(
        "ix_l2_context_manifests_case_id",
        "l2_context_manifests",
        ["case_id"],
    )

    with op.batch_alter_table("llm_call_events") as batch:
        batch.add_column(sa.Column("manifest_id", sa.String(length=64)))
        batch.add_column(
            sa.Column(
                "usage_source",
                sa.String(length=16),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.create_foreign_key(
            "fk_llm_call_manifest",
            "l2_context_manifests",
            ["manifest_id"],
            ["manifest_id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_llm_call_events_usage_source",
            "usage_source IN ('provider', 'estimated', 'unknown')",
        )
    op.create_index(
        "ix_llm_call_events_manifest_id",
        "llm_call_events",
        ["manifest_id"],
    )


def downgrade() -> None:
    """仅供本地开发回退，删除 v0.7 派生数据并恢复 v0.6 Schema。"""

    op.drop_index("ix_llm_call_events_manifest_id", table_name="llm_call_events")
    with op.batch_alter_table("llm_call_events") as batch:
        batch.drop_constraint("ck_llm_call_events_usage_source", type_="check")
        batch.drop_constraint("fk_llm_call_manifest", type_="foreignkey")
        batch.drop_column("usage_source")
        batch.drop_column("manifest_id")
    op.drop_index("ix_l2_context_manifests_case_id", table_name="l2_context_manifests")
    op.drop_table("l2_context_manifests")
    with op.batch_alter_table("l2_case_events") as batch:
        batch.drop_constraint("ck_l2_case_events_sequence", type_="check")
        batch.drop_constraint("uq_l2_case_event_sequence", type_="unique")
        batch.drop_column("context_summary_json")
        batch.drop_column("payload_version")
        batch.drop_column("sequence_no")
    with op.batch_alter_table("l2_support_cases") as batch:
        batch.drop_constraint("ck_l2_cases_event_sequence", type_="check")
        batch.drop_constraint("ck_l2_cases_trace_state", type_="check")
        batch.drop_column("next_event_sequence")
        batch.drop_column("failure_attribution")
        batch.drop_column("trace_state")
        batch.drop_column("context_policy_version")
