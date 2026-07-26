"""按生命周期模式执行无副作用、可聚合且可脱敏的部署预检。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from commerce_resolve.adapters.sqlite_business import business_schema_head
from commerce_resolve.adapters.sqlite_policy import calculate_policy_corpus_hash
from commerce_resolve.web.settings import DeploymentSettings

from .locking import instance_lock_held
from .manifest import (
    development_release_manifest,
    load_instance_manifest,
    load_release_manifest,
)
from .models import (
    CheckStatus,
    PreflightCheck,
    PreflightMode,
    PreflightReport,
    ReleaseManifest,
)


def _check(
    check_id: str,
    passed: bool,
    *,
    error_code: str,
    success: str,
    failure: str,
) -> PreflightCheck:
    """把布尔判断转换为不泄露本机配置的稳定检查结果。"""

    return PreflightCheck(
        check_id=check_id,
        status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
        error_code=None if passed else error_code,
        summary=success if passed else failure,
    )


def resolve_release_manifest(
    settings: DeploymentSettings,
    *,
    project_root: Path,
) -> ReleaseManifest:
    """部署模式读取镜像清单，开发模式允许生成明确的开发清单。"""

    if settings.release_manifest_path.is_file():
        return load_release_manifest(settings.release_manifest_path)
    if settings.deployment:
        raise ValueError("release_manifest_missing")
    return development_release_manifest(project_root)


def _sqlite_tables(path: Path) -> set[str]:
    """只读获取 SQLite 表名，避免路径拼错时创建空数据库。"""

    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
    except sqlite3.Error:
        return set()


def _business_schema_current(path: Path, expected: str) -> bool:
    """只读比较业务库 Alembic revision 与发布清单 Head。"""

    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return row is not None and str(row[0]) == expected


def _policy_index_current(source: Path, index: Path) -> bool:
    """只读确认派生索引存在且语料摘要与当前政策源一致。"""

    if not source.is_dir() or not index.is_file():
        return False
    try:
        connection = sqlite3.connect(f"file:{index.resolve()}?mode=ro", uri=True)
        try:
            metadata = {
                str(row[0]): str(row[1])
                for row in connection.execute("SELECT key, value FROM index_metadata")
            }
        finally:
            connection.close()
        return metadata.get("corpus_hash") == calculate_policy_corpus_hash(source)
    except (OSError, sqlite3.Error, ValueError):
        return False


def _path_writable_without_creation(path: Path) -> bool:
    """判断既有路径或最近既有父目录是否允许当前进程写入。"""

    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.exists() and os.access(candidate, os.W_OK | os.X_OK)


def _frontend_assets_current(path: Path, version: str) -> bool:
    """确认前端资源存在，且构建清单只声明当前后端版本。"""

    if not path.is_dir() or not any(item.is_file() for item in path.rglob("*")):
        return False
    try:
        payload = json.loads(
            (path / "release-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"app_version": version}


def run_preflight(
    settings: DeploymentSettings,
    mode: PreflightMode,
    *,
    project_root: Path,
    lock_already_held: bool = False,
) -> PreflightReport:
    """执行指定模式的全部必要检查，并一次返回所有安全错误码。"""

    checks: list[PreflightCheck] = []
    try:
        release = resolve_release_manifest(settings, project_root=project_root)
        release_valid = (
            release.app_version == release.frontend_version
            and release.business_schema_head == business_schema_head()
            and release.policy_source_hash
            == calculate_policy_corpus_hash(settings.web.policy_source_path)
            and (
                not settings.deployment
                or (
                    len(release.git_commit) == 40
                    and all(
                        character in "0123456789abcdef"
                        for character in release.git_commit.lower()
                    )
                    and release.frontend_asset_hash != "missing"
                    and release.runtime_lock_hash != "missing"
                    and release.offline_baseline_id != "development"
                )
            )
        )
    except (OSError, ValueError):
        release = None
        release_valid = False
    checks.append(
        _check(
            "release_manifest",
            release_valid,
            error_code="release_manifest_invalid",
            success="发布清单与代码、前端和迁移版本一致。",
            failure="发布清单缺失、损坏或版本不一致。",
        )
    )

    config_errors = settings.validation_errors(
        require_llm_credentials=mode
        in {PreflightMode.INIT, PreflightMode.SERVE, PreflightMode.UPGRADE}
    )
    checks.append(
        _check(
            "deployment_configuration",
            not config_errors,
            error_code=config_errors[0] if config_errors else "configuration_invalid",
            success="部署配置通过 Schema 与跨字段校验。",
            failure="部署配置不满足安全或路径约束。",
        )
    )

    writable_mode = mode in {
        PreflightMode.INIT,
        PreflightMode.SERVE,
        PreflightMode.RESTORE,
        PreflightMode.UPGRADE,
    }
    if mode == PreflightMode.BACKUP:
        data_layout_valid = settings.data_root.is_dir() and (
            _path_writable_without_creation(settings.backup_root)
        )
    else:
        data_layout_valid = (
            _path_writable_without_creation(settings.data_root)
            if writable_mode
            else settings.data_root.is_dir()
        )
    checks.append(
        _check(
            "data_layout",
            data_layout_valid,
            error_code="data_layout_invalid",
            success="数据根满足当前模式的访问要求。",
            failure="数据根缺失、不可访问或不可写。",
        )
    )

    if mode == PreflightMode.STATUS:
        checks.append(
            PreflightCheck(
                check_id="instance_lock",
                status=CheckStatus.PASSED,
                summary=(
                    "实例锁当前被服务持有。"
                    if instance_lock_held(settings.instance_lock_path)
                    else "实例锁当前空闲。"
                ),
            )
        )
    else:
        lock_available = lock_already_held or not instance_lock_held(
            settings.instance_lock_path
        )
        checks.append(
            _check(
                "instance_lock",
                lock_available,
                error_code="instance_lock_held",
                success="当前操作拥有或可取得实例锁。",
                failure="实例正在由另一个进程使用。",
            )
        )

    instance_path = settings.instance_manifest_path
    instance_required = mode not in {PreflightMode.INIT, PreflightMode.RESTORE}
    try:
        instance = load_instance_manifest(instance_path)
        instance_valid = release is not None and (
            instance.data_format_version == release.data_format_version
        )
    except ValueError:
        instance = None
        instance_valid = False
    if mode == PreflightMode.INIT and not instance_path.exists():
        instance_valid = True
    if mode == PreflightMode.RESTORE and not instance_path.exists():
        instance_valid = True
    checks.append(
        _check(
            "instance_manifest",
            instance_valid or (not instance_required and instance is None),
            error_code="instance_manifest_invalid",
            success="实例清单兼容或目标允许初始化。",
            failure="实例清单缺失、损坏或数据格式不兼容。",
        )
    )

    require_stores = mode in {
        PreflightMode.SERVE,
        PreflightMode.BACKUP,
        PreflightMode.STATUS,
    }
    if require_stores:
        expected_head = (
            release.business_schema_head if release else business_schema_head()
        )
        business_ready = _business_schema_current(
            settings.web.business_db_path, expected_head
        )
        checkpoint_ready = settings.web.checkpoint_db_path.is_file() and {
            "checkpoints",
            "writes",
        }.issubset(_sqlite_tables(settings.web.checkpoint_db_path))
        memory_ready = settings.web.memory_db_path.is_file() and {
            "store",
            "store_migrations",
        }.issubset(_sqlite_tables(settings.web.memory_db_path))
        for check_id, ready in (
            ("business_schema", business_ready),
            ("checkpoint_store", checkpoint_ready),
            ("memory_store", memory_ready),
        ):
            checks.append(
                _check(
                    check_id,
                    ready,
                    error_code=f"{check_id}_not_ready",
                    success=f"{check_id} 已就绪。",
                    failure=f"{check_id} 未就绪。",
                )
            )
        if mode != PreflightMode.BACKUP:
            policy_ready = _policy_index_current(
                settings.web.policy_source_path,
                settings.web.policy_index_db_path,
            )
            checks.append(
                _check(
                    "policy_index",
                    policy_ready,
                    error_code="policy_index_not_ready",
                    success="policy_index 已就绪。",
                    failure="policy_index 未就绪。",
                )
            )

    if mode in {
        PreflightMode.INIT,
        PreflightMode.SERVE,
        PreflightMode.UPGRADE,
        PreflightMode.STATUS,
    }:
        frontend_ready = release is not None and _frontend_assets_current(
            settings.web.frontend_dist_path,
            release.app_version,
        )
        checks.append(
            _check(
                "frontend_assets",
                frontend_ready,
                error_code="frontend_assets_missing",
                success="前端静态资源可读取。",
                failure="前端静态资源缺失。",
            )
        )

    return PreflightReport(
        mode=mode,
        passed=all(item.status != CheckStatus.FAILED for item in checks),
        checks=tuple(checks),
    )


def report_json(report: PreflightReport) -> str:
    """把预检报告序列化为稳定、适合脚本消费的 UTF-8 JSON。"""

    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
