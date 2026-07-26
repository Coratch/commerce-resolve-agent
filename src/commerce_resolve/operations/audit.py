"""写入脱敏、有限轮转且独立于业务库的运维审计。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from commerce_resolve.structured_logging import redact_log_value


def append_operation_audit(
    path: str | Path,
    *,
    operation: str,
    status: str,
    error_code: str | None = None,
    details: dict[str, object] | None = None,
    max_bytes: int = 2_000_000,
) -> None:
    """追加一条不含用户正文和 Secret 的审计，并在大小上限时轮转一次。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size >= max_bytes:
        rotated = target.with_suffix(target.suffix + ".1")
        rotated.unlink(missing_ok=True)
        os.replace(target, rotated)
    payload = {
        "schema_version": "operations-audit-v1",
        "timestamp": datetime.now(UTC).isoformat(),
        "operation": operation,
        "status": status,
        "error_code": error_code,
        "details": details or {},
    }
    with target.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                redact_log_value(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
