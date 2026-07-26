"""提供无副作用的 Liveness、Readiness 与 Capability 契约。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from commerce_resolve.operations.manifest import load_instance_manifest
from commerce_resolve.operations.models import CapabilityReport, ReleaseManifest
from commerce_resolve.operations.preflight import (
    _business_schema_current,
    _policy_index_current,
)

from .settings import DeploymentSettings


def _has_tables(path: Path, required: set[str]) -> bool:
    """用只读 SQLite 连接判断必要表是否存在，不创建缺失文件。"""

    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return required.issubset(tables)


def _frontend_matches(path: Path, version: str) -> bool:
    """读取构建时前端清单并确认与后端 Release 版本一致。"""

    manifest = path / "release-manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"app_version": version}


def readiness_state(
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> tuple[bool, str | None, CapabilityReport]:
    """只读检查本地存储、索引和前端版本，并给出稳定失败码。"""

    business_ready = _business_schema_current(
        settings.web.business_db_path,
        release.business_schema_head,
    )
    checkpoint_ready = _has_tables(
        settings.web.checkpoint_db_path,
        {"checkpoints", "writes"},
    )
    memory_ready = _has_tables(
        settings.web.memory_db_path,
        {"store", "store_migrations"},
    )
    policy_ready = _policy_index_current(
        settings.web.policy_source_path,
        settings.web.policy_index_db_path,
    )
    frontend_ready = _frontend_matches(
        settings.web.frontend_dist_path,
        release.app_version,
    )
    try:
        instance = load_instance_manifest(settings.instance_manifest_path)
        instance_ready = (
            instance.data_format_version == release.data_format_version
            and instance.last_successful_release == release.app_version
        )
    except ValueError:
        instance_ready = False
    checks = (
        ("instance_not_ready", instance_ready),
        ("business_schema_not_ready", business_ready),
        ("checkpoint_not_ready", checkpoint_ready),
        ("memory_not_ready", memory_ready),
        ("policy_index_not_ready", policy_ready),
        ("frontend_version_mismatch", frontend_ready),
    )
    error_code = next((code for code, passed in checks if not passed), None)
    core_ready = error_code is None
    llm_configured = settings.web.model_configured()
    llm_enabled = settings.web.llm_feature_enabled
    capabilities = CapabilityReport(
        guest="enabled" if core_ready else "unavailable",
        registered_llm=(
            "enabled"
            if core_ready and llm_enabled and llm_configured
            else "disabled"
            if not llm_enabled
            else "unavailable"
        ),
        refund=(
            "enabled"
            if business_ready and checkpoint_ready and policy_ready
            else "unavailable"
        ),
        l2=(
            "enabled"
            if core_ready and llm_enabled and llm_configured and memory_ready
            else "disabled"
            if not llm_enabled
            else "unavailable"
        ),
        policy_rag="enabled" if policy_ready else "unavailable",
    )
    return core_ready, error_code, capabilities


def register_health_routes(
    app: FastAPI,
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> None:
    """注册兼容 Health、独立 Live 和只读 Ready 端点。"""

    def live_payload() -> dict[str, str]:
        """仅从进程内清单投影存活状态，不访问磁盘或外部服务。"""

        return {"status": "alive", "version": release.app_version}

    @app.get("/api/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        """返回进程已运行的最小公开事实。"""

        return live_payload()

    @app.get("/api/health/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        """返回本地依赖是否可以安全承接请求，不主动修复任何组件。"""

        passed, error_code, capabilities = readiness_state(settings, release)
        payload: dict[str, object] = {
            "status": "ready" if passed else "not_ready",
            "version": release.app_version,
            "capabilities": capabilities.model_dump(mode="json"),
        }
        if error_code is not None:
            payload["error_code"] = error_code
        return JSONResponse(status_code=200 if passed else 503, content=payload)

    @app.get("/api/health", include_in_schema=False)
    def compatible_health() -> dict[str, str]:
        """保留旧 Health 路径并投影为 Liveness 契约。"""

        return live_payload()
