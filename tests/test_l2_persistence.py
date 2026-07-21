"""验证 v0.5 L2 Case、公开 Trace、计量和迁移边界。"""

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import inspect

from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository
from commerce_resolve.l2_models import (
    L2BudgetLimits,
    L2BudgetState,
    L2CaseCreate,
    L2CaseTransition,
    L2ModelCallStart,
    L2PublicTraceEvent,
)

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _repositories(
    tmp_path: Path,
) -> tuple[SqliteBusinessRepository, SqliteL2CaseRepository]:
    """创建迁移到 head 且共享 Engine 的业务与 L2 Repository。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine, now_provider=lambda: NOW)
    l2 = SqliteL2CaseRepository(engine, now_provider=lambda: NOW)
    return business, l2


def _registered_case(
    business: SqliteBusinessRepository,
    l2: SqliteL2CaseRepository,
    *,
    username: str = "l2.user",
) -> tuple[str, str, str, str]:
    """创建注册用户、conversation 和一个活动 L2 Case。"""

    invite = business.create_invitation()
    registration = business.register(
        username=username,
        password="correct horse battery",
        invitation_code=invite.code,
    )
    conversation = business.create_conversation(
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    case_id = f"case-{username}"
    l2.create_case_if_absent(
        L2CaseCreate(
            case_id=case_id,
            thread_id=conversation.thread_id,
            subject_id=registration.user.id,
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            related_order_id=None,
            issue_summary="订单物流与退款政策存在冲突",
            model_name="fake-l2",
            prompt_version="v0.5.0",
            toolset_version="v0.5.0",
            budget=L2BudgetLimits(),
        )
    )
    return (
        registration.user.id,
        registration.workspace.id,
        conversation.thread_id,
        case_id,
    )


def test_v05_migration_adds_l2_tables_without_removing_existing_tables(
    tmp_path: Path,
) -> None:
    """验证增量迁移同时保留旧业务表并增加三张 L2 表。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    tables = set(inspect(engine).get_table_names())

    assert {
        "users",
        "orders",
        "mock_refunds",
        "l2_support_cases",
        "l2_case_events",
        "llm_call_events",
    } <= tables
    engine.dispose()


def test_case_creation_trace_and_transition_are_idempotent(tmp_path: Path) -> None:
    """验证 Case、公开事件和终态更新在节点重放时不重复。"""

    business, l2 = _repositories(tmp_path)
    user_id, workspace_id, thread_id, case_id = _registered_case(business, l2)
    created = l2.get_authorized_case(
        case_id=case_id,
        subject_id=user_id,
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    assert created is not None

    event = L2PublicTraceEvent(
        event_id="event-001",
        case_id=case_id,
        event_key="case:created",
        step_number=0,
        event_type="case_created",
        result_code="created",
        created_at=NOW,
    )
    first = l2.append_event_once(
        user_id=user_id,
        workspace_id=workspace_id,
        event=event,
    )
    second = l2.append_event_once(
        user_id=user_id,
        workspace_id=workspace_id,
        event=event.model_copy(update={"event_id": "event-duplicate"}),
    )
    assert first.event_id == second.event_id == "event-001"
    assert l2.count_events() == 1

    transition = L2CaseTransition(
        expected_statuses=("l2_active",),
        status="l2_resolved",
        stop_reason="resolved",
        usage=L2BudgetState(steps_used=1),
        final_response="已完成复杂售后分析。",
    )
    resolved = l2.transition_case(
        case_id=case_id,
        user_id=user_id,
        workspace_id=workspace_id,
        transition=transition,
    )
    repeated = l2.transition_case(
        case_id=case_id,
        user_id=user_id,
        workspace_id=workspace_id,
        transition=transition,
    )
    assert resolved.status == repeated.status == "l2_resolved"
    assert repeated.completed_at is not None
    business.engine.dispose()


def test_model_call_atomically_consumes_daily_and_case_budget(tmp_path: Path) -> None:
    """验证 L2 Provider 尝试同时受每日额度与 Case 预算约束。"""

    business, l2 = _repositories(tmp_path)
    user_id, _, thread_id, case_id = _registered_case(business, l2)
    call = L2ModelCallStart(
        call_id="call-001",
        user_id=user_id,
        thread_id=thread_id,
        case_id=case_id,
        step_id="step-001",
        model_name="fake-l2",
        charged_tokens=100,
        created_at=NOW,
    )

    started = l2.begin_model_call(
        data=call,
        usage_date=date(2026, 7, 20),
        daily_limit=1,
    )
    repeated = l2.begin_model_call(
        data=call,
        usage_date=date(2026, 7, 20),
        daily_limit=1,
    )
    blocked = l2.begin_model_call(
        data=call.model_copy(update={"call_id": "call-002", "step_id": "step-002"}),
        usage_date=date(2026, 7, 20),
        daily_limit=1,
    )

    assert started is not None
    assert repeated is not None and repeated.call_id == "call-001"
    assert blocked is None
    assert l2.count_model_calls() == 1
    assert business.get_llm_usage(user_id, date(2026, 7, 20)).accepted_calls == 1

    completed = l2.finish_model_call(
        call_id="call-001",
        user_id=user_id,
        case_id=case_id,
        status="completed",
        input_tokens=70,
        output_tokens=20,
        duration_ms=15,
    )
    assert completed is not None
    assert completed.status == "completed"
    assert completed.input_tokens == 70
    business.engine.dispose()


def test_case_reads_require_complete_owner_scope(tmp_path: Path) -> None:
    """验证同一数据库中的其他用户无法读取 Case 或公开 Trace。"""

    business, l2 = _repositories(tmp_path)
    user_id, workspace_id, _, case_id = _registered_case(business, l2)
    other_invite = business.create_invitation()
    other = business.register(
        username="other.user",
        password="correct horse battery",
        invitation_code=other_invite.code,
    )

    assert (
        l2.get_authorized_case(
            case_id=case_id,
            subject_id=other.user.id,
            user_id=other.user.id,
            workspace_id=other.workspace.id,
        )
        is None
    )
    assert (
        l2.list_events(
            case_id=case_id,
            user_id=other.user.id,
            workspace_id=other.workspace.id,
        )
        == ()
    )
    assert (
        l2.get_authorized_case(
            case_id=case_id,
            subject_id=user_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        is not None
    )
    business.engine.dispose()
