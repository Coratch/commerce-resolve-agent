"""定义 v0.3 Web 服务的不可变运行配置。"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self
from urllib.parse import urlparse


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
    eval_run_root: Path = Path("var/eval/runs")
    eval_baseline_path: Path = Path("data/eval/baselines/offline-v1.3.json")
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
            eval_run_root=Path(os.getenv("EVAL_RUN_ROOT", "var/eval/runs")),
            eval_baseline_path=Path(
                os.getenv(
                    "EVAL_BASELINE_PATH",
                    "data/eval/baselines/offline-v1.3.json",
                )
            ),
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


@dataclass(frozen=True)
class DeploymentSettings:
    """保存 v1.0 单机部署、实例锁、备份和停机配置。"""

    web: WebSettings
    app_env: str = "development"
    public_base_url: str = "http://127.0.0.1:8000"
    data_root: Path = Path("var")
    backup_root: Path = Path("var/backups")
    instance_manifest_path: Path = Path("var/instance.json")
    instance_lock_path: Path = Path("var/.instance.lock")
    operations_audit_path: Path = Path("var/operations.jsonl")
    release_manifest_path: Path = Path("release-manifest.json")
    shutdown_grace_seconds: int = 30
    log_level: str = "INFO"
    operations_audit_max_bytes: int = 2_000_000

    @classmethod
    def from_env(cls, web: WebSettings | None = None) -> Self:
        """从环境变量构造部署配置，并让全部运行路径有统一数据根。"""

        selected_web = web or WebSettings.from_env()
        data_root = Path(
            os.getenv("DATA_ROOT", str(selected_web.business_db_path.parent))
        )
        return cls(
            web=selected_web,
            app_env=os.getenv("APP_ENV", "development").strip().lower(),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
            .strip()
            .rstrip("/"),
            data_root=data_root,
            backup_root=Path(os.getenv("BACKUP_ROOT", str(data_root / "backups"))),
            instance_manifest_path=Path(
                os.getenv("INSTANCE_MANIFEST_PATH", str(data_root / "instance.json"))
            ),
            instance_lock_path=Path(
                os.getenv("INSTANCE_LOCK_PATH", str(data_root / ".instance.lock"))
            ),
            operations_audit_path=Path(
                os.getenv("OPERATIONS_AUDIT_PATH", str(data_root / "operations.jsonl"))
            ),
            release_manifest_path=Path(
                os.getenv("RELEASE_MANIFEST_PATH", "release-manifest.json")
            ),
            shutdown_grace_seconds=_read_positive_int("SHUTDOWN_GRACE_SECONDS", 30),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            operations_audit_max_bytes=_read_positive_int(
                "OPERATIONS_AUDIT_MAX_BYTES", 2_000_000
            ),
        )

    @property
    def deployment(self) -> bool:
        """判断当前是否启用正式单机部署约束。"""

        return self.app_env == "deployment"

    def validation_errors(
        self,
        *,
        require_llm_credentials: bool = True,
    ) -> tuple[str, ...]:
        """返回跨字段错误码；离线运维模式可以不读取 LLM 凭据。"""

        errors: list[str] = []
        if self.app_env not in {"development", "test", "deployment"}:
            errors.append("app_env_invalid")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("log_level_invalid")
        parsed = urlparse(self.public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            errors.append("public_base_url_invalid")
        elif self.deployment and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            errors.append("public_host_not_loopback")
        if parsed.scheme == "https" and not self.web.cookie_secure:
            errors.append("secure_cookie_required")
        if self.deployment and self.web.host != "0.0.0.0":
            errors.append("container_host_invalid")
        if self.deployment and self.public_base_url not in self.web.allowed_origins:
            errors.append("public_origin_missing")
        if (
            require_llm_credentials
            and self.web.llm_feature_enabled
            and not self.web.model_configured()
        ):
            errors.append("llm_configuration_incomplete")
        data_root = self.data_root.resolve(strict=False)
        managed_paths = (
            self.web.business_db_path,
            self.web.checkpoint_db_path,
            self.web.memory_db_path,
            self.web.policy_index_db_path,
            self.backup_root,
            self.instance_manifest_path,
            self.instance_lock_path,
            self.operations_audit_path,
        )
        for path in managed_paths:
            try:
                path.resolve(strict=False).relative_to(data_root)
            except ValueError:
                errors.append("managed_path_outside_data_root")
                break
        return tuple(sorted(set(errors)))
