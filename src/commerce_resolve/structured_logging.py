"""提供脱敏 JSON 日志和跨请求关联上下文。"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)
action_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "action_id", default=None
)

_SENSITIVE_KEY_PARTS = {
    "api_key",
    "authorization",
    "base_url",
    "cookie",
    "csrf",
    "invitation",
    "password",
    "session_token",
    "secret",
}
_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_HOME_PATTERN = re.compile(r"/(?:Users|home)/[^/\s]+/")


def redact_log_value(value: object) -> object:
    """递归移除日志中的敏感键、凭据形态和用户主目录。"""

    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
                else redact_log_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_log_value(item) for item in value]
    if isinstance(value, Path):
        value = value.as_posix()
    if isinstance(value, str):
        return _HOME_PATTERN.sub(
            "/Users/[redacted]/", _TOKEN_PATTERN.sub("[redacted]", value)
        )
    return value


class JsonLogFormatter(logging.Formatter):
    """把标准 LogRecord 转换为单行、脱敏、带关联标识的 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """输出稳定公共字段，并只接纳显式 `event_fields` 扩展。"""

        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event_name", record.getMessage()),
        }
        for key, value in (
            ("request_id", request_id_var.get()),
            ("run_id", run_id_var.get()),
            ("action_id", action_id_var.get()),
        ):
            if value:
                payload[key] = value
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(
            redact_log_value(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def configure_json_logging(level: str = "INFO") -> None:
    """为根 Logger 安装唯一 JSON Handler，并避免重复注册。"""

    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.handlers[:] = [handler]


def log_event(
    logger: logging.Logger,
    level: int,
    event_name: str,
    **fields: object,
) -> None:
    """记录不含用户正文的结构化事件字段。"""

    logger.log(
        level,
        event_name,
        extra={"event_name": event_name, "event_fields": fields},
    )
