"""定义 v0.3 Web 服务的不可变运行配置。"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self


def _read_bool(name: str, default: bool) -> bool:
    """从环境变量解析严格布尔值，拒绝含糊配置。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} 必须为 true 或 false")


def _read_positive_int(name: str, default: int) -> int:
    """从环境变量读取正整数配置。"""

    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须为正整数")
    return value


@dataclass(frozen=True)
class WebSettings:
    """集中保存 Web、数据库、认证、配额和静态资源配置。"""

    business_db_path: Path = Path("var/business.sqlite")
    checkpoint_db_path: Path = Path("var/checkpoints.sqlite")
    policy_source_path: Path = Path("data/policies")
    policy_index_db_path: Path = Path("var/policy-index.sqlite")
    memory_db_path: Path = Path("var/memory.sqlite")
    frontend_dist_path: Path = Path("frontend/dist")
    host: str = "127.0.0.1"
    port: int = 8000
    session_ttl_hours: int = 24
    guest_session_ttl_hours: int = 2
    cookie_secure: bool = False
    cookie_name: str = "commerce_resolve_session"
    llm_daily_call_limit: int = 20
    llm_feature_enabled: bool = True
    chat_message_max_length: int = 2000
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )

    @classmethod
    def from_env(cls) -> Self:
        """从环境变量构造一次性配置，不在请求中重复读取。"""

        origins = tuple(
            item.strip().rstrip("/")
            for item in os.getenv(
                "WEB_ALLOWED_ORIGINS",
                "http://127.0.0.1:8000,http://localhost:8000,"
                "http://127.0.0.1:5173,http://localhost:5173",
            ).split(",")
            if item.strip()
        )
        return cls(
            business_db_path=Path(os.getenv("BUSINESS_DB_PATH", "var/business.sqlite")),
            checkpoint_db_path=Path(
                os.getenv("CHECKPOINT_DB_PATH", "var/checkpoints.sqlite")
            ),
            policy_source_path=Path(os.getenv("POLICY_SOURCE_PATH", "data/policies")),
            policy_index_db_path=Path(
                os.getenv("POLICY_INDEX_DB_PATH", "var/policy-index.sqlite")
            ),
            memory_db_path=Path(os.getenv("MEMORY_DB_PATH", "var/memory.sqlite")),
            frontend_dist_path=Path(os.getenv("FRONTEND_DIST_PATH", "frontend/dist")),
            host=os.getenv("WEB_HOST", "127.0.0.1"),
            port=_read_positive_int("WEB_PORT", 8000),
            session_ttl_hours=_read_positive_int("SESSION_TTL_HOURS", 24),
            guest_session_ttl_hours=_read_positive_int("GUEST_SESSION_TTL_HOURS", 2),
            cookie_secure=_read_bool("COOKIE_SECURE", False),
            llm_daily_call_limit=_read_positive_int("LLM_DAILY_CALL_LIMIT", 20),
            llm_feature_enabled=_read_bool("LLM_FEATURE_ENABLED", True),
            chat_message_max_length=_read_positive_int("CHAT_MESSAGE_MAX_LENGTH", 2000),
            allowed_origins=origins,
        )

    def model_configured(self) -> bool:
        """判断真实 Chat Interpreter 所需三个环境变量是否完整。"""

        return all(
            os.getenv(name, "").strip()
            for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")
        )
