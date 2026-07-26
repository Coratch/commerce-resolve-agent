"""验证重启收敛不会重放副作用或破坏待审批状态。"""

import json
from pathlib import Path

from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.operations.lifecycle import (
    initialize_instance,
    reconcile_unfinished_runs,
    runtime_status,
)
from commerce_resolve.operations.preflight import resolve_release_manifest
from commerce_resolve.web.settings import DeploymentSettings, WebSettings


def _initialized_settings(tmp_path: Path) -> DeploymentSettings:
    """创建只含空存储和前端版本清单的隔离测试实例。"""

    data = tmp_path / "var"
    frontend = tmp_path / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    (frontend / "release-manifest.json").write_text(
        json.dumps({"app_version": "2.0.0"}),
        encoding="utf-8",
    )
    settings = DeploymentSettings(
        web=WebSettings(
            business_db_path=data / "business.sqlite",
            checkpoint_db_path=data / "checkpoints.sqlite",
            memory_db_path=data / "memory.sqlite",
            policy_source_path=Path("data/policies"),
            policy_index_db_path=data / "policy-index.sqlite",
            frontend_dist_path=frontend,
            llm_feature_enabled=False,
        ),
        app_env="test",
        data_root=data,
        backup_root=data / "backups",
        instance_manifest_path=data / "instance.json",
        instance_lock_path=data / ".instance.lock",
        operations_audit_path=data / "operations.jsonl",
        release_manifest_path=tmp_path / "release.json",
    )
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    initialize_instance(settings, release)
    return settings


def test_reconciliation_interrupts_only_active_runs_and_is_repeat_safe(
    tmp_path: Path,
) -> None:
    """验证遗留 active Run 只收敛一次，已完成 Run 不被改写。"""

    settings = _initialized_settings(tmp_path)
    engine = create_business_engine(settings.web.business_db_path)
    business = SqliteBusinessRepository(engine)
    conversations = SqliteConversationRepository(engine)
    try:
        guest = business.create_guest_session()
        first_thread = business.create_conversation(
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
        )
        active = conversations.accept_chat_message(
            thread_id=first_thread.thread_id,
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
            client_request_id="active-request",
            message="合成活动请求",
        )
        conversations.mark_run_started(active.run.run_id)

        second_thread = business.create_conversation(
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
        )
        completed = conversations.accept_chat_message(
            thread_id=second_thread.thread_id,
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
            client_request_id="completed-request",
            message="合成完成请求",
        )
        conversations.complete_run(
            run_id=completed.run.run_id,
            assistant_message="已完成",
            payload={},
            pending_action=None,
        )
    finally:
        engine.dispose()

    first = reconcile_unfinished_runs(settings.web.business_db_path)
    second = reconcile_unfinished_runs(settings.web.business_db_path)
    engine = create_business_engine(settings.web.business_db_path)
    repository = SqliteConversationRepository(engine)
    try:
        assert first.interrupted_runs == 1
        assert second.interrupted_runs == 0
        assert (
            repository.get_run(
                active.run.run_id,
                thread_id=first_thread.thread_id,
            ).status
            == "interrupted"
        )
        assert (
            repository.get_run(
                completed.run.run_id,
                thread_id=second_thread.thread_id,
            ).status
            == "completed"
        )
    finally:
        engine.dispose()


def test_reconciliation_preserves_waiting_action(tmp_path: Path) -> None:
    """验证重启不会批准、拒绝或清除等待用户确认的动作。"""

    settings = _initialized_settings(tmp_path)
    engine = create_business_engine(settings.web.business_db_path)
    business = SqliteBusinessRepository(engine)
    repository = SqliteConversationRepository(engine)
    try:
        guest = business.create_guest_session()
        conversation = business.create_conversation(
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
        )
        accepted = repository.accept_chat_message(
            thread_id=conversation.thread_id,
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
            client_request_id="pending-request",
            message="需要确认",
        )
        repository.complete_run(
            run_id=accepted.run.run_id,
            assistant_message="请确认",
            payload={},
            pending_action="refund_approval",
            checkpoint_id="checkpoint-fixture",
        )
    finally:
        engine.dispose()

    report = reconcile_unfinished_runs(settings.web.business_db_path)
    engine = create_business_engine(settings.web.business_db_path)
    repository = SqliteConversationRepository(engine)
    try:
        restored = repository.get_run(
            accepted.run.run_id,
            thread_id=conversation.thread_id,
        )
        assert report.interrupted_runs == 0
        assert report.preserved_pending_actions == 1
        assert restored.status == "waiting_action"
        assert restored.pending_action == "refund_approval"
    finally:
        engine.dispose()


def test_runtime_status_contains_bounded_operational_counts(tmp_path: Path) -> None:
    """验证本机状态提供固定聚合指标且不引入用户或订单标签。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())

    status = runtime_status(settings, release)

    assert {
        "requests",
        "messages",
        "model_calls",
        "tool_results",
        "mock_refunds",
        "failed_runs",
        "interrupted_runs",
        "retry_runs",
    } <= set(status.counts)
    assert not any("user_id" in key or "order_id" in key for key in status.counts)
