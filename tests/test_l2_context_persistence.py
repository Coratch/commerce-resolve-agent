"""验证 v0.7 Manifest、模型关联、Trace 序号和只读回放持久语义。"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from commerce_resolve.adapters.sqlalchemy_models import (
    L2ContextManifestRow,
    LlmCallEventRow,
)
from commerce_resolve.adapters.sqlite_business import (
    BusinessDataError,
    SqliteBusinessRepository,
    _alembic_config,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository
from commerce_resolve.l2_context import CONTEXT_POLICY_VERSION, build_l2_context
from commerce_resolve.l2_models import (
    L2BudgetLimits,
    L2CaseCreate,
    L2ModelCallStart,
    L2PublicTraceEvent,
    L2RuntimeState,
)

NOW = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)


def _repository(tmp_path: Path):
    """创建已迁移账号、会话和 v0.7 L2 Repository。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine, now_provider=lambda: NOW)
    invitation = business.create_invitation()
    registration = business.register(
        username="context.persistence",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    conversation = business.create_conversation(
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    repository = SqliteL2CaseRepository(engine, now_provider=lambda: NOW)
    return repository, engine, registration, conversation


def _create_case(repository, registration, conversation, *, legacy: bool = False):
    """创建一条新 Case，legacy=True 时模拟没有 v0.7 策略的旧记录。"""

    return repository.create_case_if_absent(
        L2CaseCreate(
            case_id="case-legacy" if legacy else "case-001",
            thread_id=conversation.thread_id,
            subject_id=registration.user.id,
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            issue_summary="核对 ORD-001 售后状态",
            model_name="fake-l2",
            prompt_version="v0.7.0",
            toolset_version="v0.7.0",
            context_policy_version=None if legacy else CONTEXT_POLICY_VERSION,
            budget=L2BudgetLimits(),
        )
    )


def _manifest(registration):
    """构造无需外部事实即可验证的最小 Context Manifest。"""

    runtime = L2RuntimeState(
        case_id="case-001",
        phase="active",
        issue_summary="核对 ORD-001 售后状态",
        related_order_id="ORD-001",
        allowed_tools=("get_order",),
    )
    result = build_l2_context(
        runtime=runtime,
        case_id="case-001",
        step_id="step-001",
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        now=NOW,
    )
    assert result.ready
    return result.manifest


def test_manifest_is_idempotent_and_required_before_v07_model_call(
    tmp_path: Path,
) -> None:
    """验证 Manifest 幂等、冲突拒绝且 v0.7 调用必须先有关联记录。"""

    repository, engine, registration, conversation = _repository(tmp_path)
    _create_case(repository, registration, conversation)
    manifest = _manifest(registration)
    saved = repository.save_manifest_once(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        manifest=manifest,
    )
    repeated = repository.save_manifest_once(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        manifest=manifest,
    )
    common = dict(
        user_id=registration.user.id,
        thread_id=conversation.thread_id,
        case_id="case-001",
        step_id="step-001",
        model_name="fake-l2",
        charged_tokens=100,
        created_at=NOW,
    )

    rejected = repository.begin_model_call(
        data=L2ModelCallStart(call_id="call-rejected", **common),
        usage_date=date(2026, 7, 21),
        daily_limit=20,
    )
    accepted = repository.begin_model_call(
        data=L2ModelCallStart(
            call_id="call-accepted",
            manifest_id=manifest.manifest_id,
            **common,
        ),
        usage_date=date(2026, 7, 21),
        daily_limit=20,
    )

    assert saved == repeated
    assert repository.count_manifests() == 1
    assert rejected is None
    assert accepted is not None and accepted.manifest_id == manifest.manifest_id
    with pytest.raises(BusinessDataError, match="l2_manifest_conflict"):
        repository.save_manifest_once(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            manifest=manifest.model_copy(update={"pack_hash": "f" * 64}),
        )
    engine.dispose()


def test_trace_keyset_metrics_and_replay_have_zero_side_effects(tmp_path: Path) -> None:
    """验证事件序号、分页与指标读取稳定，重复回放不写入任何事实。"""

    repository, engine, registration, conversation = _repository(tmp_path)
    case = _create_case(repository, registration, conversation)
    manifest = _manifest(registration)
    repository.save_manifest_once(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        manifest=manifest,
    )
    for index in range(1, 4):
        repository.append_event_once(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            event=L2PublicTraceEvent(
                event_id=f"event-{index}",
                case_id=case.case_id,
                event_key=f"step:{index}",
                step_number=index,
                event_type="context_prepared" if index == 1 else "model_decision",
                result_code="ready",
                context_summary=manifest.public_summary if index == 1 else None,
                created_at=NOW,
            ),
        )
    before = (
        repository.count_cases(),
        repository.count_events(),
        repository.count_manifests(),
        repository.count_model_calls(),
    )
    first = repository.list_events(
        case_id=case.case_id,
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        limit=2,
    )
    second = repository.list_events(
        case_id=case.case_id,
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        after_sequence=first[-1].sequence_no,
        limit=2,
    )
    metrics = repository.get_case_metrics(
        case_id=case.case_id,
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
    )
    after = (
        repository.count_cases(),
        repository.count_events(),
        repository.count_manifests(),
        repository.count_model_calls(),
    )

    assert [item.sequence_no for item in (*first, *second)] == [1, 2, 3]
    assert metrics is not None and metrics.candidate_count > 0
    assert before == after
    engine.dispose()


def test_legacy_case_remains_partial(tmp_path: Path) -> None:
    """验证没有历史 Manifest 的旧 Case 不会被补造为完整 Trace。"""

    repository, engine, registration, conversation = _repository(tmp_path)
    case = _create_case(repository, registration, conversation, legacy=True)

    assert case.trace_state == "partial"
    assert case.context_policy_version is None
    assert repository.count_manifests() == 0
    engine.dispose()


def test_context_message_reader_requires_complete_registered_scope(
    tmp_path: Path,
) -> None:
    """验证消息正文只会在 thread、subject、user 和 workspace 全匹配后读取。"""

    repository, engine, registration, conversation = _repository(tmp_path)
    del repository
    messages = SqliteConversationRepository(engine)
    messages.accept_chat_message(
        thread_id=conversation.thread_id,
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
        client_request_id="context-reader-001",
        message="请继续处理 ORD-001 的退款问题",
    )

    authorized = messages.list_authorized_context_messages(
        thread_id=conversation.thread_id,
        subject_id=registration.user.id,
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        limit=100,
    )
    wrong_user = messages.list_authorized_context_messages(
        thread_id=conversation.thread_id,
        subject_id=registration.user.id,
        user_id="other-user",
        workspace_id=registration.workspace.id,
        limit=100,
    )
    wrong_subject = messages.list_authorized_context_messages(
        thread_id=conversation.thread_id,
        subject_id="other-subject",
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        limit=100,
    )
    wrong_workspace = messages.list_authorized_context_messages(
        thread_id=conversation.thread_id,
        subject_id=registration.user.id,
        user_id=registration.user.id,
        workspace_id="other-workspace",
        limit=100,
    )

    assert [item.content for item in authorized] == ["请继续处理 ORD-001 的退款问题"]
    assert wrong_user == wrong_subject == wrong_workspace == ()
    engine.dispose()


def test_corrupt_manifest_downgrades_public_trace_without_repair(
    tmp_path: Path,
) -> None:
    """验证损坏诊断 JSON 只标记 unavailable，且读取不会补写 Manifest。"""

    repository, engine, registration, conversation = _repository(tmp_path)
    _create_case(repository, registration, conversation)
    manifest = _manifest(registration)
    repository.save_manifest_once(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        manifest=manifest,
    )
    with engine.begin() as connection:
        connection.execute(
            update(L2ContextManifestRow)
            .where(L2ContextManifestRow.manifest_id == manifest.manifest_id)
            .values(diagnostic_items_json="{broken")
        )
    before = repository.count_manifests()

    case = repository.get_authorized_case(
        case_id="case-001",
        subject_id=registration.user.id,
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
    )
    manifests = repository.list_manifests(
        case_id="case-001",
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
    )

    assert case is not None and case.trace_state == "unavailable"
    assert manifests == ()
    assert repository.count_manifests() == before == 1
    engine.dispose()


def test_v06_rows_upgrade_in_place_with_partial_trace_and_unknown_usage(
    tmp_path: Path,
) -> None:
    """验证从 v0.6 原地升级会保留 Case、事件和模型调用并稳定回填。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine, now_provider=lambda: NOW)
    invitation = business.create_invitation()
    registration = business.register(
        username="migration.user",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    conversation = business.create_conversation(
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    repository = SqliteL2CaseRepository(engine, now_provider=lambda: NOW)
    case = repository.create_case_if_absent(
        L2CaseCreate(
            case_id="case-v06",
            thread_id=conversation.thread_id,
            subject_id=registration.user.id,
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            issue_summary="迁移前的二线客服 Case",
            model_name="fake-l2",
            prompt_version="v0.6.0",
            toolset_version="v0.6.0",
            context_policy_version=None,
            budget=L2BudgetLimits(),
        )
    )
    repository.append_event_once(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        event=L2PublicTraceEvent(
            event_id="event-v06",
            case_id=case.case_id,
            event_key="case:created",
            step_number=0,
            event_type="case_created",
            result_code="created",
            created_at=NOW,
        ),
    )
    repository.begin_model_call(
        data=L2ModelCallStart(
            call_id="call-v06",
            user_id=registration.user.id,
            thread_id=conversation.thread_id,
            case_id=case.case_id,
            step_id="step-v06",
            model_name="fake-l2",
            charged_tokens=100,
            created_at=NOW,
        ),
        usage_date=date(2026, 7, 21),
        daily_limit=20,
    )
    engine.dispose()

    config = _alembic_config(database)
    command.downgrade(config, "20260721_0004")
    command.upgrade(config, "head")

    migrated_engine = create_business_engine(database)
    migrated = SqliteL2CaseRepository(migrated_engine, now_provider=lambda: NOW)
    migrated_case = migrated.get_authorized_case(
        case_id="case-v06",
        subject_id=registration.user.id,
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
    )
    migrated_events = migrated.list_events(
        case_id="case-v06",
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
    )
    sessions = sessionmaker(migrated_engine)
    with sessions() as session:
        migrated_call = session.scalar(
            select(LlmCallEventRow).where(LlmCallEventRow.call_id == "call-v06")
        )

    assert migrated_case is not None
    assert migrated_case.trace_state == "partial"
    assert migrated_case.context_policy_version is None
    assert [event.sequence_no for event in migrated_events] == [1]
    assert migrated_call is not None
    assert migrated_call.manifest_id is None
    assert migrated_call.usage_source == "unknown"
    migrated_engine.dispose()
