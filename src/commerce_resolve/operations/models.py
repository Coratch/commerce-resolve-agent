"""定义 v1.0 运维领域的稳定 Schema 与退出码。"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperationExitCode(IntEnum):
    """定义宿主脚本可稳定判断的运维退出码。"""

    SUCCESS = 0
    INVALID_CONFIGURATION = 2
    PREFLIGHT_FAILED = 3
    INSTANCE_LOCKED = 4
    INVALID_BACKUP = 5
    RESTORE_REJECTED = 6
    UPGRADE_FAILED = 7
    SECURITY_REJECTED = 8


class PreflightMode(StrEnum):
    """限定 Preflight 支持的六种生命周期模式。"""

    INIT = "init"
    SERVE = "serve"
    BACKUP = "backup"
    RESTORE = "restore"
    UPGRADE = "upgrade"
    STATUS = "status"


class CheckStatus(StrEnum):
    """表示单项检查通过、失败或不适用。"""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReleaseManifest(BaseModel):
    """保存镜像内不可由运行环境覆盖的发布事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "release-manifest-v1"
    app_version: str
    git_commit: str
    build_timestamp: datetime
    python_version: str
    frontend_version: str
    frontend_asset_hash: str
    runtime_lock_hash: str
    npm_lock_hash: str
    policy_source_hash: str
    business_schema_head: str
    checkpoint_format: str = "langgraph-sqlite-v1"
    memory_format: str = "langgraph-store-sqlite-v1"
    data_format_version: str = "commerce-resolve-data-v1"
    offline_baseline_id: str


class InstanceManifest(BaseModel):
    """保存本地实例身份、数据格式、升级版本和恢复血缘。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "instance-manifest-v1"
    instance_id: str
    data_format_version: str = "commerce-resolve-data-v1"
    initialized_at: datetime
    last_successful_release: str
    last_successful_commit: str
    source_version: str
    restored_from: str | None = None


class PreflightCheck(BaseModel):
    """记录一项不含 Secret 和宿主绝对路径的预检结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str
    status: CheckStatus
    error_code: str | None = None
    summary: str


class PreflightReport(BaseModel):
    """汇总指定模式的全部预检事实与稳定结论。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "preflight-report-v1"
    mode: PreflightMode
    passed: bool
    checks: tuple[PreflightCheck, ...]


class BackupFileRecord(BaseModel):
    """保存 Backup Set 中一个权威文件的大小和内容摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str
    filename: str
    size_bytes: int = Field(ge=0)
    sha256: str
    sqlite_integrity: str


class BackupManifest(BaseModel):
    """保存一组可离线校验的跨库备份事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "backup-manifest-v1"
    backup_id: str
    created_at: datetime
    source_instance_id: str
    source_release: str
    source_commit: str
    data_format_version: str
    consistency: Literal["stopped-single-host"] = "stopped-single-host"
    files: tuple[BackupFileRecord, ...]
    domain_counts: dict[str, int]
    excluded: tuple[str, ...] = (
        "policy-index",
        "logs",
        "operations-audit",
        "eval-artifacts",
        "temporary-files",
        "environment-and-secrets",
        "frontend-assets",
        "existing-backups",
    )


class ReconciliationReport(BaseModel):
    """记录启动或显式收敛未完成 Run 的结构化计数。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interrupted_runs: int = Field(ge=0)
    preserved_pending_actions: int = Field(ge=0)


class CapabilityReport(BaseModel):
    """公开声明核心与可选 LLM 能力当前是否可用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    guest: Literal["enabled", "disabled", "degraded", "unavailable"]
    registered_llm: Literal["enabled", "disabled", "degraded", "unavailable"]
    refund: Literal["enabled", "disabled", "degraded", "unavailable"]
    l2: Literal["enabled", "disabled", "degraded", "unavailable"]
    policy_rag: Literal["enabled", "disabled", "degraded", "unavailable"]


class RuntimeStatus(BaseModel):
    """保存本机运维入口可读取的有限状态和聚合计数。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "runtime-status-v1"
    alive: bool
    ready: bool
    release_version: str
    release_commit: str
    instance_id: str | None
    lock_held: bool
    capabilities: CapabilityReport
    counts: dict[str, int]
    failure_codes: tuple[str, ...] = ()
