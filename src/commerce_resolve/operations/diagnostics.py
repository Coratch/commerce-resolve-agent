"""聚合本机可读、脱敏且不需要直接查询表的运维诊断。"""

from __future__ import annotations

import json
from pathlib import Path

from commerce_resolve.web.settings import DeploymentSettings

from .lifecycle import runtime_status
from .manifest import load_instance_manifest
from .models import ReleaseManifest


def recent_operations(
    path: str | Path, *, limit: int = 10
) -> tuple[dict[str, object], ...]:
    """读取最近有限运维结果，只返回事件类型、状态、错误码和时间。"""

    audit = Path(path)
    if not audit.is_file():
        return ()
    records: list[dict[str, object]] = []
    for line in audit.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(
            {
                "timestamp": payload.get("timestamp"),
                "operation": payload.get("operation"),
                "status": payload.get("status"),
                "error_code": payload.get("error_code"),
            }
        )
    return tuple(records[-max(1, min(limit, 50)) :])


def diagnostic_payload(
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> dict[str, object]:
    """返回版本、组件、能力、聚合计数和最近运维动作的本机诊断。"""

    status = runtime_status(settings, release).model_dump(mode="json")
    try:
        instance = load_instance_manifest(settings.instance_manifest_path)
        status["data_format_version"] = instance.data_format_version
        status["source_version"] = instance.source_version
        status["restored_from"] = instance.restored_from
    except ValueError:
        status["data_format_version"] = None
        status["source_version"] = None
        status["restored_from"] = None
    status["recent_operations"] = recent_operations(settings.operations_audit_path)
    return status
