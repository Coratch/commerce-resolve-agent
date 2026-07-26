"""创建、验证并恢复不包含派生索引的 SQLite Backup Set。"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from commerce_resolve.web.settings import DeploymentSettings

from .lifecycle import reset_derived_policy_index
from .locking import InstanceLock
from .manifest import (
    atomic_write_json,
    load_instance_manifest,
    new_instance_manifest,
    sha256_file,
    write_instance_manifest,
)
from .models import BackupFileRecord, BackupManifest, ReleaseManifest

AUTHORITATIVE_FILES = (
    ("business", "business.sqlite"),
    ("checkpoints", "checkpoints.sqlite"),
    ("memory", "memory.sqlite"),
)


def _sqlite_integrity(path: Path) -> str:
    """在只读连接上执行 SQLite integrity_check 并返回规范结果。"""

    try:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return "invalid"
    return str(row[0]) if row else "missing"


def _sqlite_snapshot(source: Path, target: Path) -> None:
    """使用 SQLite Online Backup API 复制一致快照，而非直接复制 WAL 文件。"""

    if not source.is_file():
        raise ValueError("authoritative_database_missing")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"file:{source.resolve()}?mode=ro",
        uri=True,
    )
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        target_connection.close()
        source_connection.close()
    for suffix in ("-wal", "-shm"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)


def _table_counts(
    database: Path,
    *,
    prefix: str,
    tables: tuple[str, ...],
) -> dict[str, int]:
    """读取固定白名单表的有限计数，并用存储角色限定键名。"""

    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        return {
            f"{prefix}.{table}": int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in tables
            if table in existing
        }
    finally:
        connection.close()


def _domain_counts(
    business: Path,
    checkpoints: Path,
    memory: Path,
) -> dict[str, int]:
    """聚合业务、Checkpoint 与 Memory 三类权威存储的恢复计数。"""

    counts = _table_counts(
        business,
        prefix="business",
        tables=(
            "users",
            "workspaces",
            "conversations",
            "conversation_messages",
            "agent_runs",
            "agent_run_events",
            "orders",
            "shipments",
            "refund_actions",
            "mock_refunds",
            "l2_support_cases",
            "l2_case_events",
            "l2_context_manifests",
            "llm_call_events",
        ),
    )
    counts.update(
        _table_counts(
            checkpoints,
            prefix="checkpoints",
            tables=("checkpoints", "writes", "checkpoint_blobs"),
        )
    )
    counts.update(
        _table_counts(
            memory,
            prefix="memory",
            tables=("store", "store_migrations"),
        )
    )
    return counts


def _record(logical_name: str, path: Path, *, sqlite_file: bool) -> BackupFileRecord:
    """为备份文件生成摘要、长度与完整性记录。"""

    return BackupFileRecord(
        logical_name=logical_name,
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        sqlite_integrity=_sqlite_integrity(path) if sqlite_file else "not_applicable",
    )


def _ensure_under(path: Path, root: Path) -> Path:
    """规范化运维路径并拒绝逃逸允许的 Backup Root。"""

    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError as error:
        raise ValueError("backup_path_outside_root") from error
    return resolved


def _create_backup_unlocked(
    settings: DeploymentSettings,
    release: ReleaseManifest,
    output_root: Path,
) -> Path:
    """在调用方已持有实例锁时创建并原子发布 Backup Set。"""

    instance = load_instance_manifest(settings.instance_manifest_path)
    root = _ensure_under(output_root, settings.backup_root)
    root.mkdir(parents=True, exist_ok=True)
    backup_id = (
        f"backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    final = root / backup_id
    with TemporaryDirectory(dir=root, prefix=".backup-") as directory:
        temporary = Path(directory)
        source_paths = {
            "business": settings.web.business_db_path,
            "checkpoints": settings.web.checkpoint_db_path,
            "memory": settings.web.memory_db_path,
        }
        records: list[BackupFileRecord] = []
        for logical_name, filename in AUTHORITATIVE_FILES:
            target = temporary / filename
            _sqlite_snapshot(source_paths[logical_name], target)
            records.append(_record(logical_name, target, sqlite_file=True))
        instance_target = temporary / "instance.json"
        shutil.copyfile(settings.instance_manifest_path, instance_target)
        records.append(_record("instance_manifest", instance_target, sqlite_file=False))
        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=datetime.now(UTC),
            source_instance_id=instance.instance_id,
            source_release=instance.last_successful_release,
            source_commit=instance.last_successful_commit,
            data_format_version=instance.data_format_version,
            files=tuple(records),
            domain_counts=_domain_counts(
                temporary / "business.sqlite",
                temporary / "checkpoints.sqlite",
                temporary / "memory.sqlite",
            ),
        )
        atomic_write_json(
            temporary / "backup-manifest.json",
            manifest.model_dump(mode="json"),
        )
        verified = verify_backup(temporary, allowed_root=root)
        if verified.backup_id != backup_id:
            raise ValueError("backup_identity_mismatch")
        os.replace(temporary, final)
    return final


def create_backup(
    settings: DeploymentSettings,
    release: ReleaseManifest,
    *,
    output_root: Path | None = None,
) -> Path:
    """取得实例独占锁后创建一致 Backup Set，运行中服务会立即拒绝。"""

    with InstanceLock(settings.instance_lock_path):
        return _create_backup_unlocked(
            settings,
            release,
            output_root or settings.backup_root,
        )


def verify_backup(
    backup: str | Path,
    *,
    allowed_root: Path,
) -> BackupManifest:
    """在修改目标前校验清单、必需文件、摘要和 SQLite 完整性。"""

    directory = _ensure_under(Path(backup), allowed_root)
    manifest_path = directory / "backup-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("backup_manifest_invalid")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = BackupManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("backup_manifest_invalid") from error
    expected_names = {filename for _, filename in AUTHORITATIVE_FILES} | {
        "instance.json"
    }
    directory_names = {item.name for item in directory.iterdir()}
    if directory_names != expected_names | {"backup-manifest.json"}:
        raise ValueError("backup_file_set_invalid")
    actual_names = {record.filename for record in manifest.files}
    if actual_names != expected_names:
        raise ValueError("backup_file_set_invalid")
    expected_roles = {logical_name for logical_name, _ in AUTHORITATIVE_FILES} | {
        "instance_manifest"
    }
    if {record.logical_name for record in manifest.files} != expected_roles:
        raise ValueError("backup_file_roles_invalid")
    for record in manifest.files:
        path = directory / record.filename
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != record.size_bytes
            or sha256_file(path) != record.sha256
        ):
            raise ValueError("backup_file_digest_mismatch")
        if record.sqlite_integrity != "not_applicable":
            if _sqlite_integrity(path) != "ok":
                raise ValueError("backup_sqlite_integrity_failed")
    if (
        _domain_counts(
            directory / "business.sqlite",
            directory / "checkpoints.sqlite",
            directory / "memory.sqlite",
        )
        != manifest.domain_counts
    ):
        raise ValueError("backup_domain_counts_mismatch")
    return manifest


def _ensure_restore_capacity(settings: DeploymentSettings, required_bytes: int) -> None:
    """在创建暂存副本前确认同一数据卷有基础空间余量。"""

    candidate = settings.data_root
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    free_bytes = shutil.disk_usage(candidate).free
    reserve = max(1_048_576, required_bytes // 10)
    if free_bytes < required_bytes + reserve:
        raise ValueError("restore_insufficient_space")


def _validate_restored_stores(
    business: Path,
    checkpoints: Path,
    memory: Path,
    release: ReleaseManifest,
) -> None:
    """验证恢复候选的业务 Schema、Checkpoint 和 Memory 基础格式。"""

    if not _immutable_business_schema_current(
        business,
        release.business_schema_head,
    ):
        raise ValueError("restore_business_schema_incompatible")
    if not {"checkpoints", "writes"}.issubset(_immutable_sqlite_tables(checkpoints)):
        raise ValueError("restore_checkpoint_format_incompatible")
    if not {"store", "store_migrations"}.issubset(_immutable_sqlite_tables(memory)):
        raise ValueError("restore_memory_format_incompatible")


def _immutable_sqlite_tables(path: Path) -> set[str]:
    """读取已封存 Backup SQLite 的表名且不创建 WAL 辅助文件。"""

    try:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro&immutable=1",
            uri=True,
        )
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


def _immutable_business_schema_current(path: Path, expected: str) -> bool:
    """读取封存业务库的迁移版本且不创建 WAL 辅助文件。"""

    try:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return row is not None and str(row[0]) == expected


def restore_backup(
    settings: DeploymentSettings,
    release: ReleaseManifest,
    *,
    backup: Path,
    replace: bool = False,
    confirm_instance_id: str | None = None,
) -> tuple[BackupManifest, Path | None]:
    """验证后恢复到空目标；覆盖时要求实例 ID 并先创建回滚备份。"""

    manifest = verify_backup(backup, allowed_root=settings.backup_root)
    if manifest.data_format_version != release.data_format_version:
        raise ValueError("backup_data_format_incompatible")
    required_bytes = sum(
        item.size_bytes for item in manifest.files if item.filename.endswith(".sqlite")
    )
    _ensure_restore_capacity(settings, required_bytes)
    directory = Path(backup).resolve()
    _validate_restored_stores(
        directory / "business.sqlite",
        directory / "checkpoints.sqlite",
        directory / "memory.sqlite",
        release,
    )
    settings.data_root.mkdir(parents=True, exist_ok=True)
    rollback: Path | None = None
    with InstanceLock(settings.instance_lock_path):
        target_exists = settings.instance_manifest_path.is_file()
        if target_exists and not replace:
            raise ValueError("restore_target_not_empty")
        if target_exists:
            current = load_instance_manifest(settings.instance_manifest_path)
            if not confirm_instance_id or confirm_instance_id != current.instance_id:
                raise ValueError("restore_instance_confirmation_required")
            rollback = _create_backup_unlocked(
                settings,
                release,
                settings.backup_root / "rollback",
            )
            target_instance_id = current.instance_id
            initialized_at = current.initialized_at
        else:
            new_manifest = new_instance_manifest(
                release,
                source_version="restore",
                restored_from=manifest.backup_id,
            )
            target_instance_id = new_manifest.instance_id
            initialized_at = new_manifest.initialized_at

        with TemporaryDirectory(dir=settings.data_root, prefix=".restore-") as staging:
            stage = Path(staging)
            for _logical_name, filename in AUTHORITATIVE_FILES:
                shutil.copyfile(directory / filename, stage / filename)
                if _sqlite_integrity(stage / filename) != "ok":
                    raise ValueError("restore_staging_integrity_failed")
            targets = {
                "business.sqlite": settings.web.business_db_path,
                "checkpoints.sqlite": settings.web.checkpoint_db_path,
                "memory.sqlite": settings.web.memory_db_path,
            }
            for filename, target in targets.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / filename, target)

        restored_counts = _domain_counts(
            settings.web.business_db_path,
            settings.web.checkpoint_db_path,
            settings.web.memory_db_path,
        )
        if restored_counts != manifest.domain_counts:
            raise ValueError("restore_domain_counts_mismatch")

        restored = new_instance_manifest(
            release,
            source_version="restore",
            restored_from=manifest.backup_id,
        ).model_copy(
            update={
                "instance_id": target_instance_id,
                "initialized_at": initialized_at,
            }
        )
        reset_derived_policy_index(settings)
        write_instance_manifest(settings.instance_manifest_path, restored)
    return manifest, rollback
