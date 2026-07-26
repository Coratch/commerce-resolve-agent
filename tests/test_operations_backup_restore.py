"""验证 Backup Set 的完整性、排除项、空目标与覆盖保护。"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from commerce_resolve.operations.backup import (
    create_backup,
    restore_backup,
    verify_backup,
)
from commerce_resolve.operations.manifest import load_instance_manifest
from commerce_resolve.operations.preflight import resolve_release_manifest
from tests.test_operations_lifecycle import _initialized_settings


def test_backup_manifest_contains_only_authoritative_data(tmp_path: Path) -> None:
    """验证 Backup Set 包含三库和实例清单，并排除索引、日志与配置。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    settings.operations_audit_path.write_text("synthetic audit\n", encoding="utf-8")

    backup = create_backup(settings, release)
    manifest = verify_backup(backup, allowed_root=settings.backup_root)
    names = {item.filename for item in manifest.files}

    assert names == {
        "business.sqlite",
        "checkpoints.sqlite",
        "memory.sqlite",
        "instance.json",
    }
    assert manifest.consistency == "stopped-single-host"
    assert "environment-and-secrets" in manifest.excluded
    assert "checkpoints.checkpoints" in manifest.domain_counts
    assert "memory.store" in manifest.domain_counts
    assert not (backup / "policy-index.sqlite").exists()
    assert not (backup / "operations.jsonl").exists()
    assert all(
        item.sqlite_integrity == "ok"
        for item in manifest.files
        if item.filename.endswith(".sqlite")
    )


def test_backup_with_extra_file_is_rejected(tmp_path: Path) -> None:
    """验证 Backup Set 中任何未声明文件都会使整体校验失败。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    backup = create_backup(settings, release)
    (backup / "unexpected.txt").write_text("synthetic", encoding="utf-8")

    with pytest.raises(ValueError, match="backup_file_set_invalid"):
        verify_backup(backup, allowed_root=settings.backup_root)


def test_corrupt_backup_is_rejected_before_target_write(tmp_path: Path) -> None:
    """验证摘要损坏在创建目标数据库或实例清单前被拒绝。"""

    source = _initialized_settings(tmp_path / "source")
    release = resolve_release_manifest(source, project_root=Path.cwd())
    backup = create_backup(source, release)
    with (backup / "business.sqlite").open("ab") as stream:
        stream.write(b"corruption")
    target = _initialized_settings(tmp_path / "target")
    target.web.business_db_path.unlink()
    target.web.checkpoint_db_path.unlink()
    target.web.memory_db_path.unlink()
    target.web.policy_index_db_path.unlink()
    target.instance_manifest_path.unlink()
    target_backup = target.backup_root / backup.name
    target.backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(backup, target_backup)

    with pytest.raises(ValueError, match="backup_file_digest_mismatch"):
        restore_backup(target, release, backup=target_backup)

    assert not target.web.business_db_path.exists()
    assert not target.instance_manifest_path.exists()


def test_restore_to_empty_target_preserves_data_and_new_identity(
    tmp_path: Path,
) -> None:
    """验证空目标恢复业务计数、重建索引并保留目标自己的实例身份。"""

    source = _initialized_settings(tmp_path / "source")
    release = resolve_release_manifest(source, project_root=Path.cwd())
    with sqlite3.connect(source.web.business_db_path) as connection:
        source_count = connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[
            0
        ]
    backup = create_backup(source, release)
    source_instance = load_instance_manifest(source.instance_manifest_path)

    target = _initialized_settings(tmp_path / "target")
    for path in (
        target.web.business_db_path,
        target.web.checkpoint_db_path,
        target.web.memory_db_path,
        target.web.policy_index_db_path,
        target.instance_manifest_path,
    ):
        path.unlink()
    target.backup_root.mkdir(parents=True, exist_ok=True)
    target_backup = target.backup_root / backup.name
    shutil.copytree(backup, target_backup)

    restored_manifest, rollback = restore_backup(
        target,
        release,
        backup=target_backup,
    )
    target_instance = load_instance_manifest(target.instance_manifest_path)

    assert rollback is None
    assert target_instance.instance_id != source_instance.instance_id
    assert target_instance.restored_from == restored_manifest.backup_id
    assert target.web.policy_index_db_path.is_file()
    with sqlite3.connect(target.web.business_db_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
            == source_count
        )


def test_replace_requires_instance_confirmation_and_creates_rollback(
    tmp_path: Path,
) -> None:
    """验证非空目标只有确认实例 ID 后才覆盖，并先保留原数据快照。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    backup = create_backup(settings, release)
    instance = load_instance_manifest(settings.instance_manifest_path)

    with pytest.raises(ValueError, match="restore_instance_confirmation_required"):
        restore_backup(
            settings,
            release,
            backup=backup,
            replace=True,
            confirm_instance_id="wrong-instance",
        )

    _manifest, rollback = restore_backup(
        settings,
        release,
        backup=backup,
        replace=True,
        confirm_instance_id=instance.instance_id,
    )

    assert rollback is not None
    assert (rollback / "backup-manifest.json").is_file()
    assert (
        load_instance_manifest(settings.instance_manifest_path).instance_id
        == instance.instance_id
    )
