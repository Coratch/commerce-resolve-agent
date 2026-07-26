"""验证 JSON 日志、运维审计和本机诊断不会泄露敏感信息。"""

import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from commerce_resolve.operations.audit import append_operation_audit
from commerce_resolve.operations.diagnostics import diagnostic_payload
from commerce_resolve.operations.preflight import resolve_release_manifest
from commerce_resolve.structured_logging import (
    JsonLogFormatter,
    action_id_var,
    request_id_var,
    run_id_var,
)
from commerce_resolve.web.app import create_app
from tests.test_operations_lifecycle import _initialized_settings


def test_json_formatter_redacts_credentials_home_and_sensitive_keys() -> None:
    """验证凭据形态、用户主目录和敏感字段不会进入结构化日志。"""

    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "provider failed",
        (),
        None,
    )
    record.event_name = "provider.failed"
    synthetic_token = "sk-" + "abcdefghijklmnop"
    second_token = "sk-" + "secondsecretvalue"
    record.event_fields = {
        "api_key": synthetic_token,
        "llm_api_key": second_token,
        "provider_base_url": "https://provider.invalid/private",
        "path": "/Users/private-user/project/file.py",
        "error_code": "provider_unavailable",
    }
    token = request_id_var.set("request-log-1")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        request_id_var.reset(token)

    encoded = json.dumps(payload)
    assert payload["request_id"] == "request-log-1"
    assert payload["api_key"] == "[redacted]"
    assert "private-user" not in encoded
    assert synthetic_token not in encoded
    assert second_token not in encoded
    assert "provider.invalid" not in encoded


def test_json_formatter_propagates_run_and_action_context() -> None:
    """验证后台 Agent 日志可以关联 Run 与审批动作。"""

    formatter = JsonLogFormatter()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "done", (), None)
    run_token = run_id_var.set("run-12345678")
    action_token = action_id_var.set("action-12345678")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        action_id_var.reset(action_token)
        run_id_var.reset(run_token)

    assert payload["run_id"] == "run-12345678"
    assert payload["action_id"] == "action-12345678"


def test_operations_audit_rotates_and_redacts_details(tmp_path: Path) -> None:
    """验证审计达到上限后有限轮转，且嵌套 Secret 被替换。"""

    audit = tmp_path / "operations.jsonl"
    append_operation_audit(
        audit,
        operation="ops.init",
        status="failed",
        details={"password": "not-for-log", "path": "/Users/owner/private"},
        max_bytes=1,
    )
    append_operation_audit(
        audit,
        operation="ops.init",
        status="succeeded",
        details={"instance": "synthetic"},
        max_bytes=1,
    )

    assert audit.with_suffix(".jsonl.1").is_file()
    content = audit.with_suffix(".jsonl.1").read_text(encoding="utf-8")
    assert "not-for-log" not in content
    assert "owner" not in content
    assert "[redacted]" in content


def test_diagnostics_exposes_counts_without_user_dimensions(tmp_path: Path) -> None:
    """验证本机状态只返回有限聚合，不包含账号、订单号或本地绝对路径。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    payload = diagnostic_payload(settings, release)
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["release_version"] == "2.0.0"
    assert {
        "users",
        "orders",
        "conversations",
        "agent_runs",
        "refund_actions",
        "l2_support_cases",
        "requests",
        "messages",
        "model_calls",
        "tool_results",
        "mock_refunds",
        "failed_runs",
        "interrupted_runs",
        "retry_runs",
    } == set(payload["counts"])
    assert str(tmp_path) not in encoded
    assert "user_id" not in encoded


def test_http_log_uses_route_template_instead_of_conversation_id(
    tmp_path: Path,
    caplog,
) -> None:
    """验证请求日志不会把动态 conversation 标识作为路径或指标标签。"""

    settings = _initialized_settings(tmp_path)
    release = resolve_release_manifest(settings, project_root=Path.cwd())
    app = create_app(
        settings=settings.web,
        deployment_settings=settings,
        release_manifest=release,
        mount_spa=False,
    )
    with caplog.at_level(logging.INFO, logger="commerce_resolve.http"):
        with TestClient(app) as client:
            response = client.get("/api/conversations/sensitive-thread-value")

    assert response.status_code == 401
    record = next(
        item
        for item in caplog.records
        if getattr(item, "event_name", None) == "http.request.completed"
    )
    assert record.event_fields["route"] == "/api/conversations/{thread_id}"
    assert "sensitive-thread-value" not in json.dumps(record.event_fields)
