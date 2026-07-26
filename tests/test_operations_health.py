"""验证公开 Health 契约无副作用且能区分 Alive 与 Ready。"""

import json
from argparse import Namespace
from pathlib import Path

from fastapi.testclient import TestClient

from commerce_resolve.operations.cli import run_operations_command
from commerce_resolve.operations.models import OperationExitCode
from commerce_resolve.operations.preflight import resolve_release_manifest
from commerce_resolve.web.app import create_app
from tests.test_operations_lifecycle import _initialized_settings


def test_health_reports_live_ready_and_disabled_llm(tmp_path: Path) -> None:
    """验证禁用 LLM 时核心仍 Ready，且公开能力明确显示降级状态。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    app = create_app(
        settings=settings.web,
        deployment_settings=settings,
        release_manifest=release,
        mount_spa=False,
    )
    with TestClient(app) as client:
        live = client.get("/api/health/live")
        ready = client.get("/api/health/ready")
        compatible = client.get("/api/health")

    assert live.json() == {"status": "alive", "version": "2.0.0"}
    assert compatible.json() == live.json()
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["capabilities"]["registered_llm"] == "disabled"


def test_readiness_failure_does_not_recreate_missing_store(tmp_path: Path) -> None:
    """验证存储缺失时 Ready 返回稳定 503，且健康检查不会创建空文件。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    app = create_app(
        settings=settings.web,
        deployment_settings=settings,
        release_manifest=release,
        mount_spa=False,
    )
    settings.web.memory_db_path.unlink()
    with TestClient(app) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["error_code"] == "memory_not_ready"
    assert not settings.web.memory_db_path.exists()


def test_request_id_is_propagated_or_safely_replaced(tmp_path: Path) -> None:
    """验证合法关联标识透传，非法超长值被服务器生成值替代。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    app = create_app(
        settings=settings.web,
        deployment_settings=settings,
        release_manifest=release,
        mount_spa=False,
    )
    with TestClient(app) as client:
        accepted = client.get(
            "/api/health/live",
            headers={"X-Request-ID": "request-1234"},
        )
        replaced = client.get(
            "/api/health/live",
            headers={"X-Request-ID": "../unsafe"},
        )

    assert accepted.headers["x-request-id"] == "request-1234"
    assert replaced.headers["x-request-id"] != "../unsafe"
    assert len(replaced.headers["x-request-id"]) == 32


def test_local_status_includes_preflight_checks(tmp_path: Path, capsys) -> None:
    """验证本机诊断聚合 Preflight 结果并用退出码反映总体状态。"""

    settings = _initialized_settings(tmp_path)

    exit_code = run_operations_command(
        Namespace(ops_command="status", format="json"),
        settings,
        project_root=Path.cwd(),
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == OperationExitCode.SUCCESS
    assert payload["ready"] is True
    assert payload["checks"]
    assert payload["failure_codes"] == []
