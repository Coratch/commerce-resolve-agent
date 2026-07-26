"""使用 32 条确定性场景验证 v1.0 单机交付与运维边界。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from commerce_resolve import __version__
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.operations.backup import (
    create_backup,
    restore_backup,
    verify_backup,
)
from commerce_resolve.operations.lifecycle import (
    initialize_instance,
    reconcile_unfinished_runs,
)
from commerce_resolve.operations.locking import InstanceLock, InstanceLockUnavailable
from commerce_resolve.operations.manifest import (
    load_instance_manifest,
    sha256_file,
)
from commerce_resolve.operations.preflight import resolve_release_manifest
from commerce_resolve.operations.upgrade import build_v08_fixture, upgrade_from_v08
from commerce_resolve.release_checks import check_sensitive_artifacts
from commerce_resolve.structured_logging import JsonLogFormatter, request_id_var
from commerce_resolve.web.app import create_app
from commerce_resolve.web.health import readiness_state
from commerce_resolve.web.settings import DeploymentSettings, WebSettings


class OperationsEvalScenario(BaseModel):
    """描述一条 v1.0 运维能力及其预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    description: str
    expected_status: str = "passed"


class OperationsEvalResult(BaseModel):
    """保存单条场景的脱敏结果，不保留路径或异常正文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    passed: bool
    expected_status: str
    actual_status: str
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class OperationsEvalReport(BaseModel):
    """汇总 v1.0 的 32 条单机交付 Eval。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    operational_safety_violations: int
    results: tuple[OperationsEvalResult, ...]


def _scenarios() -> tuple[OperationsEvalScenario, ...]:
    """按已接受 Plan 的固定顺序返回 32 条场景。"""

    definitions = (
        ("release-manifest-consistent", "bundle", "发布与前端版本一致"),
        ("clean-instance-init", "bundle", "空实例可完整初始化"),
        ("repeat-init-idempotent", "bundle", "重复初始化不写重复数据"),
        ("missing-required-config-rejected", "bundle", "缺失模型配置被拒绝"),
        ("unsafe-public-origin-rejected", "bundle", "非回环公开地址被拒绝"),
        ("invalid-data-layout-rejected", "bundle", "越界数据路径被拒绝"),
        ("single-instance-lock-enforced", "lifecycle", "实例锁阻止第二写入者"),
        ("graceful-inflight-run-completes", "lifecycle", "已完成 Run 不被重写"),
        ("shutdown-timeout-run-interrupted", "lifecycle", "超时 Run 收敛为中断"),
        ("startup-running-run-reconciled", "lifecycle", "启动收敛活动 Run"),
        ("pending-action-preserved", "lifecycle", "待审批动作保持不变"),
        ("reconciliation-repeat-safe", "lifecycle", "重复收敛无新增副作用"),
        ("liveness-zero-side-effect", "health", "Liveness 不写权威数据"),
        ("readiness-storage-failure", "health", "存储缺失时拒绝 Ready"),
        ("llm-disabled-core-ready", "health", "禁用模型时核心仍 Ready"),
        ("llm-enabled-missing-config-fails", "health", "启用模型但配置缺失时失败"),
        ("logs-redacted-and-correlated", "health", "日志脱敏并带关联标识"),
        ("backup-manifest-complete", "backup", "Backup 清单与三库完整"),
        ("backup-excludes-derived-and-secrets", "backup", "Backup 排除派生与敏感文件"),
        ("backup-refuses-running-instance", "backup", "运行实例拒绝备份"),
        ("corrupt-backup-rejected-before-write", "backup", "损坏备份在写目标前失败"),
        (
            "restore-empty-target-preserves-ownership",
            "backup",
            "空目标恢复使用新实例身份",
        ),
        ("replace-requires-instance-confirmation", "backup", "覆盖恢复要求实例确认"),
        ("v08-fixture-upgrades", "upgrade", "代表性 v0.8 数据可升级"),
        ("upgrade-requires-valid-backup", "upgrade", "升级前产生可验证备份"),
        ("upgrade-failure-remains-not-ready", "upgrade", "升级失败保持非 Ready"),
        ("frontend-backend-version-mismatch", "upgrade", "前后端版本不一致时非 Ready"),
        ("container-nonroot-readonly", "security", "容器非 root 且根只读"),
        ("loopback-publish-only", "security", "宿主仅发布回环端口"),
        ("bundle-sensitive-scan-clean", "security", "候选 Bundle 无敏感产物"),
        ("release-gate-includes-deployment", "security", "发布门禁包含部署检查"),
        ("restored-policy-index-rebuilt", "security", "恢复后重建政策索引"),
    )
    return tuple(
        OperationsEvalScenario(
            scenario_id=scenario_id,
            category=category,
            description=description,
        )
        for scenario_id, category, description in definitions
    )


OPERATIONS_EVAL_SCENARIOS = _scenarios()


def _project_root() -> Path:
    """返回当前源码所属项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _settings(
    root: Path,
    *,
    frontend_version: str = __version__,
    llm_enabled: bool = False,
) -> DeploymentSettings:
    """创建完全位于临时目录的运维 Eval 配置。"""

    data = root / "var"
    frontend = root / "dist"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text("synthetic", encoding="utf-8")
    (frontend / "release-manifest.json").write_text(
        json.dumps({"app_version": frontend_version}),
        encoding="utf-8",
    )
    return DeploymentSettings(
        web=WebSettings(
            business_db_path=data / "business.sqlite",
            checkpoint_db_path=data / "checkpoints.sqlite",
            memory_db_path=data / "memory.sqlite",
            policy_source_path=_project_root() / "data/policies",
            policy_index_db_path=data / "policy-index.sqlite",
            frontend_dist_path=frontend,
            llm_feature_enabled=llm_enabled,
        ),
        app_env="test",
        data_root=data,
        backup_root=data / "backups",
        instance_manifest_path=data / "instance.json",
        instance_lock_path=data / ".instance.lock",
        operations_audit_path=data / "operations.jsonl",
        release_manifest_path=root / "release.json",
    )


def _try(action) -> bool:
    """执行无参数校验动作，并把异常稳定投影为失败。"""

    try:
        return bool(action())
    except Exception:
        return False


def _collect_bundle_evidence(evidence: dict[str, bool], root: Path) -> None:
    """执行版本、初始化、配置和数据布局场景。"""

    settings = _settings(root / "instance")
    release = resolve_release_manifest(settings, project_root=_project_root())
    package = json.loads(
        (_project_root() / "frontend/package.json").read_text(encoding="utf-8")
    )
    evidence["release-manifest-consistent"] = (
        release.app_version
        == release.frontend_version
        == package["version"]
        == __version__
    )
    first, first_manifest = initialize_instance(settings, release)
    evidence["clean-instance-init"] = first == "initialized" and all(
        path.is_file()
        for path in (
            settings.web.business_db_path,
            settings.web.checkpoint_db_path,
            settings.web.memory_db_path,
            settings.web.policy_index_db_path,
            settings.instance_manifest_path,
        )
    )
    second, second_manifest = initialize_instance(settings, release)
    evidence["repeat-init-idempotent"] = (
        second == "already_initialized" and second_manifest == first_manifest
    )
    with patch.dict(os.environ, {}, clear=True):
        missing = _settings(root / "missing", llm_enabled=True)
        evidence["missing-required-config-rejected"] = (
            "llm_configuration_incomplete" in missing.validation_errors()
        )
    unsafe = DeploymentSettings(
        web=WebSettings(
            llm_feature_enabled=False,
            host="0.0.0.0",
            allowed_origins=("http://example.com",),
        ),
        app_env="deployment",
        public_base_url="http://example.com",
    )
    evidence["unsafe-public-origin-rejected"] = (
        "public_host_not_loopback" in unsafe.validation_errors()
    )
    invalid = DeploymentSettings(
        web=WebSettings(
            business_db_path=root / "outside/business.sqlite",
            llm_feature_enabled=False,
        ),
        data_root=root / "managed",
    )
    evidence["invalid-data-layout-rejected"] = (
        "managed_path_outside_data_root" in invalid.validation_errors()
    )


def _collect_lifecycle_evidence(evidence: dict[str, bool], root: Path) -> None:
    """执行实例锁、Run 收敛、待审批和幂等场景。"""

    settings = _settings(root)
    release = resolve_release_manifest(settings, project_root=_project_root())
    initialize_instance(settings, release)
    with InstanceLock(settings.instance_lock_path):
        evidence["single-instance-lock-enforced"] = _try(
            lambda: _second_lock_rejected(settings.instance_lock_path)
        )

    engine = create_business_engine(settings.web.business_db_path)
    business = SqliteBusinessRepository(engine)
    conversations = SqliteConversationRepository(engine)
    try:
        guest = business.create_guest_session()
        completed_thread = business.create_conversation(
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
        )
        completed = conversations.accept_chat_message(
            thread_id=completed_thread.thread_id,
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
            client_request_id="v10-completed",
            message="synthetic completed",
        )
        conversations.complete_run(
            run_id=completed.run.run_id,
            assistant_message="completed",
            payload={},
            pending_action=None,
        )
        running_thread = business.create_conversation(
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
        )
        running = conversations.accept_chat_message(
            thread_id=running_thread.thread_id,
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
            client_request_id="v10-running",
            message="synthetic running",
        )
        conversations.mark_run_started(running.run.run_id)
        pending_thread = business.create_conversation(
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
        )
        pending = conversations.accept_chat_message(
            thread_id=pending_thread.thread_id,
            subject_id=guest.subject_id,
            workspace_id=guest.workspace_id,
            access_mode="guest",
            client_request_id="v10-pending",
            message="synthetic pending",
        )
        conversations.complete_run(
            run_id=pending.run.run_id,
            assistant_message="pending",
            payload={},
            pending_action="refund_approval",
            checkpoint_id="synthetic-checkpoint",
        )
    finally:
        engine.dispose()

    first = reconcile_unfinished_runs(settings.web.business_db_path)
    second = reconcile_unfinished_runs(settings.web.business_db_path)
    engine = create_business_engine(settings.web.business_db_path)
    conversations = SqliteConversationRepository(engine)
    try:
        completed_state = conversations.get_run(
            completed.run.run_id,
            thread_id=completed_thread.thread_id,
        )
        running_state = conversations.get_run(
            running.run.run_id,
            thread_id=running_thread.thread_id,
        )
        pending_state = conversations.get_run(
            pending.run.run_id,
            thread_id=pending_thread.thread_id,
        )
    finally:
        engine.dispose()
    evidence["graceful-inflight-run-completes"] = completed_state.status == "completed"
    evidence["shutdown-timeout-run-interrupted"] = running_state.status == "interrupted"
    evidence["startup-running-run-reconciled"] = first.interrupted_runs == 1
    evidence["pending-action-preserved"] = (
        pending_state.status == "waiting_action"
        and pending_state.pending_action == "refund_approval"
        and first.preserved_pending_actions == 1
    )
    evidence["reconciliation-repeat-safe"] = second.interrupted_runs == 0


def _second_lock_rejected(path: Path) -> bool:
    """尝试取得第二把实例锁，并确认立即得到稳定冲突。"""

    try:
        with InstanceLock(path):
            return False
    except InstanceLockUnavailable:
        return True


def _collect_health_evidence(evidence: dict[str, bool], root: Path) -> None:
    """执行无副作用健康检查、能力降级和日志脱敏场景。"""

    settings = _settings(root / "healthy")
    release = resolve_release_manifest(settings, project_root=_project_root())
    initialize_instance(settings, release)
    authoritative = (
        settings.web.business_db_path,
        settings.web.checkpoint_db_path,
        settings.web.memory_db_path,
    )
    app = create_app(
        settings=settings.web,
        deployment_settings=settings,
        release_manifest=release,
        mount_spa=False,
    )
    with TestClient(app) as client:
        before = tuple(sha256_file(path) for path in authoritative)
        live = client.get(
            "/api/health/live",
            headers={"X-Request-ID": "v10-eval-request"},
        )
        after = tuple(sha256_file(path) for path in authoritative)
        ready = client.get("/api/health/ready")
    evidence["liveness-zero-side-effect"] = live.status_code == 200 and before == after
    evidence["llm-disabled-core-ready"] = (
        ready.status_code == 200
        and ready.json()["capabilities"]["registered_llm"] == "disabled"
    )

    missing = _settings(root / "missing-store")
    missing_release = resolve_release_manifest(missing, project_root=_project_root())
    initialize_instance(missing, missing_release)
    missing_app = create_app(
        settings=missing.web,
        deployment_settings=missing,
        release_manifest=missing_release,
        mount_spa=False,
    )
    missing.web.memory_db_path.unlink()
    with TestClient(missing_app) as client:
        response = client.get("/api/health/ready")
    evidence["readiness-storage-failure"] = (
        response.status_code == 503
        and response.json().get("error_code") == "memory_not_ready"
        and not missing.web.memory_db_path.exists()
    )
    with patch.dict(os.environ, {}, clear=True):
        llm = _settings(root / "llm-missing", llm_enabled=True)
        evidence["llm-enabled-missing-config-fails"] = (
            "llm_configuration_incomplete" in llm.validation_errors()
        )

    formatter = JsonLogFormatter()
    record = logging.LogRecord("eval", logging.ERROR, __file__, 1, "failed", (), None)
    record.event_name = "provider.failed"
    synthetic_token = "sk-" + "abcdefghijklmnop"
    record.event_fields = {
        "api_key": synthetic_token,
        "path": "/Users/private-user/project",
    }
    token = request_id_var.set("v10-eval-request")
    try:
        encoded = formatter.format(record)
    finally:
        request_id_var.reset(token)
    evidence["logs-redacted-and-correlated"] = (
        "v10-eval-request" in encoded
        and "private-user" not in encoded
        and synthetic_token not in encoded
    )


def _collect_backup_evidence(evidence: dict[str, bool], root: Path) -> None:
    """执行 Backup 清单、锁、损坏拒绝和两类 Restore 场景。"""

    source = _settings(root / "source")
    release = resolve_release_manifest(source, project_root=_project_root())
    initialize_instance(source, release)
    backup = create_backup(source, release)
    manifest = verify_backup(backup, allowed_root=source.backup_root)
    names = {item.filename for item in manifest.files}
    evidence["backup-manifest-complete"] = names == {
        "business.sqlite",
        "checkpoints.sqlite",
        "memory.sqlite",
        "instance.json",
    }
    evidence["backup-excludes-derived-and-secrets"] = not any(
        (backup / name).exists()
        for name in ("policy-index.sqlite", "operations.jsonl", ".env")
    )
    with InstanceLock(source.instance_lock_path):
        evidence["backup-refuses-running-instance"] = not _try(
            lambda: create_backup(source, release)
        )

    corrupt_target = _settings(root / "corrupt-target")
    corrupt_target.backup_root.mkdir(parents=True, exist_ok=True)
    corrupt = corrupt_target.backup_root / "backup-corrupt"
    shutil.copytree(backup, corrupt)
    with (corrupt / "business.sqlite").open("ab") as stream:
        stream.write(b"corrupt")
    evidence["corrupt-backup-rejected-before-write"] = (
        not _try(
            lambda: restore_backup(
                corrupt_target,
                release,
                backup=corrupt,
            )
        )
        and not corrupt_target.web.business_db_path.exists()
        and not corrupt_target.instance_manifest_path.exists()
    )

    target = _settings(root / "empty-target")
    target.backup_root.mkdir(parents=True, exist_ok=True)
    target_backup = target.backup_root / backup.name
    shutil.copytree(backup, target_backup)
    source_instance = load_instance_manifest(source.instance_manifest_path)
    restored, rollback = restore_backup(target, release, backup=target_backup)
    target_instance = load_instance_manifest(target.instance_manifest_path)
    evidence["restore-empty-target-preserves-ownership"] = (
        rollback is None
        and restored.backup_id == manifest.backup_id
        and target_instance.instance_id != source_instance.instance_id
        and target_instance.restored_from == manifest.backup_id
    )
    evidence["replace-requires-instance-confirmation"] = not _try(
        lambda: restore_backup(
            source,
            release,
            backup=backup,
            replace=True,
            confirm_instance_id="wrong-instance",
        )
    )
    evidence["restored-policy-index-rebuilt"] = (
        target.web.policy_index_db_path.is_file()
    )


def _collect_upgrade_evidence(evidence: dict[str, bool], root: Path) -> None:
    """执行 v0.8 成功升级、失败保留和前端版本门禁场景。"""

    settings = _settings(root / "success")
    release = resolve_release_manifest(settings, project_root=_project_root())
    fixture = _project_root() / "data/operations/v0.8-upgrade-fixture.json"
    build_v08_fixture(settings, release, fixture)
    upgraded, backup = upgrade_from_v08(settings, release)
    verified = verify_backup(backup, allowed_root=settings.backup_root)
    with sqlite3.connect(settings.web.business_db_path) as connection:
        counts = {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in ("users", "orders", "refund_actions", "l2_support_cases")
        }
    evidence["v08-fixture-upgrades"] = (
        upgraded.last_successful_release == __version__
        and counts
        == {"users": 1, "orders": 5, "refund_actions": 2, "l2_support_cases": 1}
    )
    evidence["upgrade-requires-valid-backup"] = (
        verified.backup_id == backup.name and verified.source_release == "0.8.0"
    )

    failed = _settings(root / "failed")
    failed_release = resolve_release_manifest(failed, project_root=_project_root())
    build_v08_fixture(failed, failed_release, fixture)
    failed.web.checkpoint_db_path.unlink()
    upgrade_failed = not _try(lambda: upgrade_from_v08(failed, failed_release))
    ready, _code, _capabilities = readiness_state(failed, failed_release)
    evidence["upgrade-failure-remains-not-ready"] = (
        upgrade_failed
        and not ready
        and load_instance_manifest(
            failed.instance_manifest_path
        ).last_successful_release
        == "0.8.0"
    )

    mismatch = _settings(root / "mismatch", frontend_version="0.9.0")
    mismatch_release = resolve_release_manifest(mismatch, project_root=_project_root())
    initialize_instance(mismatch, mismatch_release)
    ready, code, _capabilities = readiness_state(mismatch, mismatch_release)
    evidence["frontend-backend-version-mismatch"] = (
        not ready and code == "frontend_version_mismatch"
    )


def _collect_security_evidence(evidence: dict[str, bool]) -> None:
    """验证 Compose 权限、端口、敏感扫描和发布门禁覆盖。"""

    root = _project_root()
    compose = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]
    evidence["container-nonroot-readonly"] = (
        app["user"] == "10001:10001"
        and app["read_only"] is True
        and app["cap_drop"] == ["ALL"]
        and "no-new-privileges:true" in app["security_opt"]
    )
    evidence["loopback-publish-only"] = str(app["ports"][0]).startswith("127.0.0.1:")
    evidence["bundle-sensitive-scan-clean"] = _try(
        lambda: check_sensitive_artifacts(root) is None
    )
    from commerce_resolve.eval_release import build_release_checks

    with TemporaryDirectory() as directory:
        identifiers = {
            item.check_id for item in build_release_checks(root, Path(directory))
        }
    evidence["release-gate-includes-deployment"] = {
        "deployment-bundle",
        "deployment-operations-tests",
        "docker-compose-config",
    }.issubset(identifiers)


def _collect_evidence() -> dict[str, bool]:
    """在隔离临时目录运行全部运维行为并返回场景布尔证据。"""

    evidence: dict[str, bool] = {}
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for collector, child in (
            (_collect_bundle_evidence, root / "bundle"),
            (_collect_lifecycle_evidence, root / "lifecycle"),
            (_collect_health_evidence, root / "health"),
            (_collect_backup_evidence, root / "backup"),
            (_collect_upgrade_evidence, root / "upgrade"),
        ):
            try:
                collector(evidence, child)
            except Exception:
                continue
        try:
            _collect_security_evidence(evidence)
        except Exception:
            pass
    return evidence


def run_operations_eval_suite(
    *,
    forced_failure: str | None = None,
) -> OperationsEvalReport:
    """运行 32 条固定场景，并允许测试注入一个稳定失败 ID。"""

    evidence = _collect_evidence()
    results: list[OperationsEvalResult] = []
    for scenario in OPERATIONS_EVAL_SCENARIOS:
        passed = evidence.get(scenario.scenario_id, False)
        if scenario.scenario_id == forced_failure:
            passed = False
        results.append(
            OperationsEvalResult(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                passed=passed,
                expected_status=scenario.expected_status,
                actual_status="passed" if passed else "failed",
                error_type=None if passed else "verification_failed",
            )
        )
    category_counts = dict(Counter(item.category for item in results))
    passed_count = sum(item.passed for item in results)
    violations = sum(len(item.safety_violations) for item in results)
    return OperationsEvalReport(
        suite="v1.0-single-host-delivery",
        total_scenarios=len(results),
        passed_scenarios=passed_count,
        passed=passed_count == len(results) and violations == 0,
        category_counts=category_counts,
        operational_safety_violations=violations,
        results=tuple(results),
    )
