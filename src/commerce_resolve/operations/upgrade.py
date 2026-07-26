"""编排从受支持 v0.8 单机实例到 v1.0 的可恢复升级。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from commerce_resolve.adapters.fake import build_fake_dependencies
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    assert_business_schema_current,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository
from commerce_resolve.adapters.sqlite_policy import SqlitePolicyRepository
from commerce_resolve.adapters.sqlite_refunds import SqliteRefundRepository
from commerce_resolve.business_models import (
    MockPaymentInput,
    OrderCreate,
    ShipmentInput,
)
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.l2_memory import (
    assert_memory_store_ready,
    confirm_preference,
    open_sqlite_memory_store,
)
from commerce_resolve.l2_models import (
    L2BudgetLimits,
    L2CaseCreate,
    L2ContextManifest,
    L2ContextPublicSummary,
    L2PublicTraceEvent,
    MemoryProposal,
)
from commerce_resolve.models import RefundPreview, RefundReason
from commerce_resolve.refund_rules import build_facts_fingerprint
from commerce_resolve.state import RunContext
from commerce_resolve.web.settings import DeploymentSettings
from commerce_resolve.workflow import build_workflow

from .backup import _create_backup_unlocked
from .lifecycle import initialize_instance, reset_derived_policy_index
from .locking import InstanceLock
from .manifest import load_instance_manifest, write_instance_manifest
from .models import InstanceManifest, ReleaseManifest
from .preflight import _sqlite_tables


def _assert_checkpoint_ready(path: Path) -> None:
    """只读验证 v0.8 Checkpoint 表仍符合当前兼容格式。"""

    if not path.is_file() or not {"checkpoints", "writes"}.issubset(
        _sqlite_tables(path)
    ):
        raise RuntimeError("checkpoint_format_incompatible")


def upgrade_from_v08(
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> tuple[InstanceManifest, Path]:
    """在独占锁内先备份，再兼容检查、迁移、重建索引并最后更新清单。"""

    with InstanceLock(settings.instance_lock_path):
        current = load_instance_manifest(settings.instance_manifest_path)
        if current.last_successful_release not in {"0.8.0", "v0.8"}:
            raise ValueError("upgrade_source_not_v08")
        backup = _create_backup_unlocked(
            settings,
            release,
            settings.backup_root / "pre-upgrade",
        )
        upgrade_business_database(settings.web.business_db_path)
        engine = create_business_engine(settings.web.business_db_path)
        try:
            assert_business_schema_current(engine, settings.web.business_db_path)
        finally:
            engine.dispose()
        _assert_checkpoint_ready(settings.web.checkpoint_db_path)
        assert_memory_store_ready(settings.web.memory_db_path)
        reset_derived_policy_index(settings)
        upgraded = current.model_copy(
            update={
                "last_successful_release": release.app_version,
                "last_successful_commit": release.git_commit,
                "source_version": "v0.8",
            }
        )
        write_instance_manifest(settings.instance_manifest_path, upgraded)
        return upgraded, backup


def _fixture_preview(
    repository: SqliteRefundRepository,
    *,
    user_id: str,
    workspace_id: str,
    order_id: str,
    task_id: str,
    action_id: str,
) -> RefundPreview:
    """根据合成订单当前事实创建可由仓库执行的稳定退款预览。"""

    context = repository.get_refund_context(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id=order_id,
    )
    policy_fact_ids = ("refund.eligibility.pre_fulfillment",)
    fingerprint = build_facts_fingerprint(
        context,
        policy_version="fixture-policy-v0.8",
        policy_fact_ids=policy_fact_ids,
    )
    return RefundPreview(
        action_id=action_id,
        task_id=task_id,
        order_id=order_id,
        reason=RefundReason(code="quality_issue"),
        amount_minor=context.paid_amount_minor,
        display_amount=f"{context.paid_amount_minor / 100:.2f}",
        currency="CNY",
        channel="mock_card",
        order_status=context.order_status,
        shipment_status=context.shipment_status,
        payment_status="settled",
        policy_fact_ids=policy_fact_ids,
        citations=(),
        policy_version="fixture-policy-v0.8",
        facts_fingerprint=fingerprint,
        preview_hash=action_id[-1] * 64,
    )


def build_v08_fixture(
    settings: DeploymentSettings,
    release: ReleaseManifest,
    fixture_path: Path,
) -> dict[str, int]:
    """从版本化合成 JSON 构建包含业务、Checkpoint、L2 与 Memory 的 v0.8 实例。"""

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != "v0.8-upgrade-fixture-v1":
        raise ValueError("v08_fixture_schema_invalid")
    initialize_instance(settings, release)
    engine = create_business_engine(settings.web.business_db_path)
    business = SqliteBusinessRepository(engine)
    conversations = SqliteConversationRepository(engine)
    refunds = SqliteRefundRepository(engine)
    l2 = SqliteL2CaseRepository(engine)
    try:
        invitation = business.create_invitation(max_uses=2)
        registration = business.register(
            username=str(fixture["username"]),
            password="synthetic-upgrade-password",
            invitation_code=invitation.code,
        )
        business.create_registered_session(registration)
        user_id = registration.user.id
        workspace_id = registration.workspace.id
        for order_id in fixture["order_ids"]:
            business.create_order(
                user_id=user_id,
                workspace_id=workspace_id,
                data=OrderCreate(
                    order_id=order_id,
                    status="processing",
                    shipment=ShipmentInput(
                        status="preparing",
                        last_event="合成升级夹具等待揽收",
                    ),
                ),
            )
            refunds.upsert_payment(
                user_id=user_id,
                workspace_id=workspace_id,
                order_id=order_id,
                data=MockPaymentInput(
                    amount="129.90",
                    currency="CNY",
                    channel="mock_card",
                    status="settled",
                ),
            )

        completed_thread = business.create_conversation(
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
        )
        completed = conversations.accept_chat_message(
            thread_id=completed_thread.thread_id,
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
            client_request_id="fixture-completed",
            message="合成已完成请求",
        )
        conversations.mark_run_started(completed.run.run_id)
        conversations.complete_run(
            run_id=completed.run.run_id,
            assistant_message="合成请求已完成",
            payload={},
            pending_action=None,
        )

        pending_thread = business.create_conversation(
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
        )
        pending = conversations.accept_chat_message(
            thread_id=pending_thread.thread_id,
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
            client_request_id="fixture-pending",
            message="合成退款待审批",
        )
        pending_preview = _fixture_preview(
            refunds,
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=fixture["order_ids"][0],
            task_id=pending_thread.thread_id,
            action_id="fixture-pending-action",
        )
        refunds.reserve_preview(
            user_id=user_id,
            workspace_id=workspace_id,
            preview=pending_preview,
        )
        conversations.complete_run(
            run_id=pending.run.run_id,
            assistant_message="等待合成退款审批",
            payload={"action_id": pending_preview.action_id},
            pending_action="refund_approval",
            checkpoint_id="fixture-checkpoint",
        )

        refunded_thread = business.create_conversation(
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
        )
        completed_preview = _fixture_preview(
            refunds,
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=fixture["order_ids"][1],
            task_id=refunded_thread.thread_id,
            action_id="fixture-completed-action",
        )
        refunds.reserve_preview(
            user_id=user_id,
            workspace_id=workspace_id,
            preview=completed_preview,
        )
        refunds.execute_refund(
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=refunded_thread.thread_id,
            action_id=completed_preview.action_id,
            expected_fingerprint=completed_preview.facts_fingerprint,
        )

        l2_thread = business.create_conversation(
            subject_id=user_id,
            workspace_id=workspace_id,
            access_mode="registered",
        )
        case_id = "fixture-l2-case"
        l2.create_case_if_absent(
            L2CaseCreate(
                case_id=case_id,
                thread_id=l2_thread.thread_id,
                subject_id=user_id,
                user_id=user_id,
                workspace_id=workspace_id,
                related_order_id=fixture["order_ids"][0],
                issue_summary="合成复杂售后问题",
                model_name="fixture-model",
                prompt_version="v0.8",
                toolset_version="v0.8",
                context_policy_version="context-policy-v0.7",
                budget=L2BudgetLimits(),
            )
        )
        l2.append_event_once(
            user_id=user_id,
            workspace_id=workspace_id,
            event=L2PublicTraceEvent(
                event_id="fixture-trace-event",
                case_id=case_id,
                event_key="fixture-created",
                step_number=0,
                event_type="case.created",
                result_code="created",
                created_at=datetime.now(UTC),
            ),
        )
        l2.save_manifest_once(
            user_id=user_id,
            workspace_id=workspace_id,
            manifest=L2ContextManifest(
                manifest_id="fixture-context-manifest",
                case_id=case_id,
                step_id="fixture-step",
                context_policy_version="context-policy-v0.7",
                scope_fingerprint="a" * 64,
                pack_hash="b" * 64,
                essential_complete=True,
                candidate_count=1,
                selected_count=1,
                duplicate_count=0,
                irrelevant_count=0,
                stale_count=0,
                conflict_count=0,
                out_of_scope_count=0,
                truncated_count=0,
                refresh_count=0,
                candidate_estimated_tokens=10,
                selected_estimated_tokens=10,
                pack_estimated_input_tokens=10,
                input_budget_tokens=1000,
                reduction_basis_points=0,
                truncated=False,
                public_summary=L2ContextPublicSummary(
                    source_types=("case_goal",),
                    selected_count=1,
                    public_evidence_ids=(),
                ),
                context_preparation_ms=1,
                created_at=datetime.now(UTC),
            ),
        )
    finally:
        engine.dispose()

    with open_sqlite_memory_store(settings.web.memory_db_path) as store:
        confirm_preference(
            store,
            user_id=user_id,
            workspace_id=workspace_id,
            proposal=MemoryProposal(
                proposal_id="fixture-memory-proposal",
                case_id=case_id,
                memory_type="response_detail",
                value="concise",
                purpose="后续客服采用该回复详细程度",
            ),
        )
    with open_sqlite_checkpointer(settings.web.checkpoint_db_path) as checkpointer:
        graph = build_workflow(
            build_fake_dependencies(
                policy_repository=SqlitePolicyRepository(
                    settings.web.policy_index_db_path,
                    source_root=settings.web.policy_source_path,
                )
            ),
            checkpointer=checkpointer,
        )
        graph.invoke(
            {"messages": [{"role": "user", "content": "查询 ORD-001"}]},
            config={"configurable": {"thread_id": "fixture-graph-thread"}},
            context=RunContext(user_id="user-001"),
        )

    current = load_instance_manifest(settings.instance_manifest_path)
    write_instance_manifest(
        settings.instance_manifest_path,
        current.model_copy(
            update={
                "last_successful_release": "0.8.0",
                "last_successful_commit": "0" * 40,
                "source_version": "v0.8",
            }
        ),
    )
    engine = create_business_engine(settings.web.business_db_path)
    try:
        return {
            "users": SqliteBusinessRepository(engine).count_users(),
            "orders": len(
                SqliteBusinessRepository(engine).list_orders(
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            ),
            "refunds": SqliteRefundRepository(engine).count_refunds(),
            "l2_cases": SqliteL2CaseRepository(engine).count_cases(),
        }
    finally:
        engine.dispose()
