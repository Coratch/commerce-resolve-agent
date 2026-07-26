"""验证固定 v0.8 合成实例可升级，失败时保留版本与回退备份。"""

import sqlite3
from pathlib import Path

import pytest

from commerce_resolve.l2_memory import open_sqlite_memory_store
from commerce_resolve.operations.manifest import load_instance_manifest
from commerce_resolve.operations.preflight import resolve_release_manifest
from commerce_resolve.operations.upgrade import build_v08_fixture, upgrade_from_v08
from tests.test_operations_lifecycle import _initialized_settings


def _checkpoint_count(path: Path) -> int:
    """读取固定夹具中至少一个 LangGraph Checkpoint 的数量。"""

    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0])


def test_v08_fixture_upgrades_without_losing_representative_data(
    tmp_path: Path,
) -> None:
    """验证账号、订单、退款、L2、Checkpoint 与偏好在升级后仍可读取。"""

    settings = _initialized_settings(tmp_path)
    for path in (
        settings.web.business_db_path,
        settings.web.checkpoint_db_path,
        settings.web.memory_db_path,
        settings.web.policy_index_db_path,
        settings.instance_manifest_path,
    ):
        path.unlink()
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    before = build_v08_fixture(
        settings,
        release,
        Path("data/operations/v0.8-upgrade-fixture.json"),
    )
    checkpoint_count = _checkpoint_count(settings.web.checkpoint_db_path)
    with open_sqlite_memory_store(settings.web.memory_db_path) as store:
        preference_count = len(store.search(("commerce-resolve",), limit=10))

    upgraded, backup = upgrade_from_v08(settings, release)

    assert before == {"users": 1, "orders": 5, "refunds": 1, "l2_cases": 1}
    assert checkpoint_count >= 1
    assert preference_count == 1
    assert upgraded.last_successful_release == "2.0.0"
    assert upgraded.source_version == "v0.8"
    assert (backup / "backup-manifest.json").is_file()
    assert load_instance_manifest(settings.instance_manifest_path) == upgraded


def test_upgrade_failure_keeps_v08_manifest_and_verified_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证升级中途失败不写成功版本，并保留已完成的升级前备份。"""

    settings = _initialized_settings(tmp_path)
    for path in (
        settings.web.business_db_path,
        settings.web.checkpoint_db_path,
        settings.web.memory_db_path,
        settings.web.policy_index_db_path,
        settings.instance_manifest_path,
    ):
        path.unlink()
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    build_v08_fixture(
        settings,
        release,
        Path("data/operations/v0.8-upgrade-fixture.json"),
    )

    def fail_index(_settings) -> None:
        """注入政策索引重建失败，模拟升级中途故障。"""

        raise RuntimeError("synthetic_upgrade_failure")

    monkeypatch.setattr(
        "commerce_resolve.operations.upgrade.reset_derived_policy_index",
        fail_index,
    )
    with pytest.raises(RuntimeError, match="synthetic_upgrade_failure"):
        upgrade_from_v08(settings, release)

    instance = load_instance_manifest(settings.instance_manifest_path)
    backups = list(
        (settings.backup_root / "pre-upgrade").glob("backup-*/backup-manifest.json")
    )
    assert instance.last_successful_release == "0.8.0"
    assert len(backups) == 1
