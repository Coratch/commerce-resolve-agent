"""验证部署配置、实例初始化和 Preflight 的无副作用失败语义。"""

import json
from dataclasses import replace
from pathlib import Path

from commerce_resolve.operations.lifecycle import initialize_instance
from commerce_resolve.operations.models import PreflightMode
from commerce_resolve.operations.preflight import (
    resolve_release_manifest,
    run_preflight,
)
from commerce_resolve.web.settings import DeploymentSettings, WebSettings


def _settings(tmp_path: Path) -> DeploymentSettings:
    """构造所有运行文件都限制在测试数据根的开发配置。"""

    data = tmp_path / "var"
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    (frontend / "release-manifest.json").write_text(
        json.dumps({"app_version": "2.0.0"}),
        encoding="utf-8",
    )
    web = WebSettings(
        business_db_path=data / "business.sqlite",
        checkpoint_db_path=data / "checkpoints.sqlite",
        memory_db_path=data / "memory.sqlite",
        policy_source_path=Path("data/policies"),
        policy_index_db_path=data / "policy-index.sqlite",
        frontend_dist_path=frontend,
        llm_feature_enabled=False,
    )
    return DeploymentSettings(
        web=web,
        app_env="test",
        data_root=data,
        backup_root=data / "backups",
        instance_manifest_path=data / "instance.json",
        instance_lock_path=data / ".instance.lock",
        operations_audit_path=data / "operations.jsonl",
        release_manifest_path=tmp_path / "missing-release.json",
    )


def test_init_is_empty_and_idempotent(tmp_path: Path) -> None:
    """验证初始化只创建存储与清单，重复执行不创建任何业务演示数据。"""

    settings = _settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    first_status, first = initialize_instance(settings, release)
    second_status, second = initialize_instance(settings, release)

    assert first_status == "initialized"
    assert second_status == "already_initialized"
    assert second == first
    import sqlite3

    with sqlite3.connect(settings.web.business_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_serve_preflight_passes_after_explicit_init(tmp_path: Path) -> None:
    """验证四类本地存储、前端和派生索引完整时实例可启动。"""

    settings = _settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    initialize_instance(settings, release)

    report = run_preflight(
        settings,
        PreflightMode.SERVE,
        project_root=Path.cwd(),
    )

    assert report.passed is True
    assert all(item.error_code is None for item in report.checks)


def test_serve_preflight_reports_corrupt_checkpoint_without_crashing(
    tmp_path: Path,
) -> None:
    """验证损坏的 Checkpoint 只产生稳定失败码，不中断完整预检报告。"""

    settings = _settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    initialize_instance(settings, release)
    settings.web.checkpoint_db_path.write_bytes(b"not-a-sqlite-database")

    report = run_preflight(
        settings,
        PreflightMode.SERVE,
        project_root=Path.cwd(),
    )

    assert report.passed is False
    assert "checkpoint_store_not_ready" in {item.error_code for item in report.checks}


def test_init_preflight_requires_frontend_release_assets(tmp_path: Path) -> None:
    """验证初始化不会接受缺失前端版本清单的非完整交付物。"""

    settings = _settings(tmp_path)
    (settings.web.frontend_dist_path / "release-manifest.json").unlink()

    report = run_preflight(
        settings,
        PreflightMode.INIT,
        project_root=Path.cwd(),
    )

    assert report.passed is False
    assert "frontend_assets_missing" in {item.error_code for item in report.checks}


def test_backup_preflight_ignores_optional_llm_and_derived_policy_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证停机备份只依赖三类权威存储，不读取模型密钥或派生索引。"""

    settings = _settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    initialize_instance(settings, release)
    settings.web.policy_index_db_path.unlink()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    backup_settings = replace(
        settings,
        web=replace(settings.web, llm_feature_enabled=True),
    )

    report = run_preflight(
        backup_settings,
        PreflightMode.BACKUP,
        project_root=Path.cwd(),
    )

    assert report.passed is True
    assert "llm_configuration_incomplete" not in {
        item.error_code for item in report.checks
    }
    assert "policy_index_not_ready" not in {item.error_code for item in report.checks}


def test_deployment_preflight_rejects_incomplete_llm_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证启用 LLM 但缺少配置时在创建数据库和实例清单前失败。"""

    settings = _settings(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    unsafe = replace(
        settings,
        web=replace(settings.web, llm_feature_enabled=True),
    )

    report = run_preflight(
        unsafe,
        PreflightMode.INIT,
        project_root=Path.cwd(),
    )

    assert report.passed is False
    assert "llm_configuration_incomplete" in {item.error_code for item in report.checks}
    assert not unsafe.web.business_db_path.exists()
    assert not unsafe.instance_manifest_path.exists()
