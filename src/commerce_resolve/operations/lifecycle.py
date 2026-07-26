"""实现实例初始化、启动收敛与本机只读状态聚合。"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from commerce_resolve.adapters.sqlite_business import (
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.adapters.sqlite_policy import build_policy_index
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.l2_memory import setup_memory_store
from commerce_resolve.web.settings import DeploymentSettings

from .locking import InstanceLock, instance_lock_held
from .manifest import (
    load_instance_manifest,
    new_instance_manifest,
    write_instance_manifest,
)
from .models import (
    InstanceManifest,
    ReconciliationReport,
    ReleaseManifest,
    RuntimeStatus,
)


def _move_initialized_file(source: Path, target: Path) -> None:
    """把临时初始化文件原子安装到数据根，并拒绝覆盖既有数据。"""

    if target.exists():
        raise FileExistsError("authoritative_store_already_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def initialize_instance(
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> tuple[str, InstanceManifest]:
    """在独占锁内初始化四类存储，并最后原子写实例清单。"""

    settings.data_root.mkdir(parents=True, exist_ok=True)
    with InstanceLock(settings.instance_lock_path):
        if settings.instance_manifest_path.is_file():
            existing = load_instance_manifest(settings.instance_manifest_path)
            if existing.data_format_version != release.data_format_version:
                raise ValueError("instance_data_format_incompatible")
            return "already_initialized", existing

        authoritative = (
            settings.web.business_db_path,
            settings.web.checkpoint_db_path,
            settings.web.memory_db_path,
        )
        if any(path.exists() and path.stat().st_size > 0 for path in authoritative):
            raise ValueError("unmanaged_existing_data")
        for path in (*authoritative, settings.web.policy_index_db_path):
            if path.exists() and path.stat().st_size == 0:
                path.unlink()

        installed: list[Path] = []
        try:
            with TemporaryDirectory(dir=settings.data_root) as directory:
                temporary = Path(directory)
                business = temporary / "business.sqlite"
                checkpoint = temporary / "checkpoints.sqlite"
                memory = temporary / "memory.sqlite"
                policy = temporary / "policy-index.sqlite"
                upgrade_business_database(business)
                with open_sqlite_checkpointer(checkpoint) as saver:
                    saver.setup()
                setup_memory_store(memory)
                build_policy_index(settings.web.policy_source_path, policy)
                for source, target in (
                    (business, settings.web.business_db_path),
                    (checkpoint, settings.web.checkpoint_db_path),
                    (memory, settings.web.memory_db_path),
                    (policy, settings.web.policy_index_db_path),
                ):
                    _move_initialized_file(source, target)
                    installed.append(target)
            manifest = new_instance_manifest(release)
            write_instance_manifest(settings.instance_manifest_path, manifest)
            return "initialized", manifest
        except Exception:
            for path in installed:
                path.unlink(missing_ok=True)
            raise


def reconcile_unfinished_runs(
    business_database: Path,
) -> ReconciliationReport:
    """把重启后不再执行的 Run 收敛为 interrupted，并保留待审批动作。"""

    engine = create_business_engine(business_database)
    try:
        repository = SqliteConversationRepository(engine)
        interrupted = repository.interrupt_unfinished_runs()
        with sqlite3.connect(business_database) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM conversations WHERE pending_action IS NOT NULL"
            ).fetchone()
        return ReconciliationReport(
            interrupted_runs=interrupted,
            preserved_pending_actions=int(row[0]) if row else 0,
        )
    finally:
        engine.dispose()


def _safe_table_count(database: Path, table: str) -> int:
    """只读统计允许表；缺失或损坏时返回零而不创建数据库。"""

    allowed = {
        "users",
        "orders",
        "conversations",
        "agent_runs",
        "refund_actions",
        "l2_support_cases",
    }
    if table not in allowed or not database.is_file():
        return 0
    try:
        connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _safe_query_count(database: Path, query: str) -> int:
    """执行代码内固定的聚合查询；存储不可用时返回零。"""

    if not database.is_file():
        return 0
    try:
        connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
        try:
            row = connection.execute(query).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _operational_counts(database: Path) -> dict[str, int]:
    """聚合请求、模型、工具、退款和恢复状态，不使用用户字段作标签。"""

    queries = {
        "requests": "SELECT COUNT(*) FROM agent_runs",
        "messages": "SELECT COUNT(*) FROM conversation_messages",
        "model_calls": "SELECT COUNT(*) FROM llm_call_events",
        "tool_results": (
            "SELECT COUNT(*) FROM l2_case_events WHERE event_type = 'tool_result'"
        ),
        "mock_refunds": "SELECT COUNT(*) FROM mock_refunds",
        "failed_runs": "SELECT COUNT(*) FROM agent_runs WHERE status = 'failed'",
        "interrupted_runs": (
            "SELECT COUNT(*) FROM agent_runs WHERE status = 'interrupted'"
        ),
        "retry_runs": (
            "SELECT COUNT(*) FROM agent_runs WHERE retry_of_run_id IS NOT NULL"
        ),
    }
    return {name: _safe_query_count(database, query) for name, query in queries.items()}


def runtime_status(
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> RuntimeStatus:
    """聚合不含身份、正文和高基数标签的本机状态。"""

    from commerce_resolve.web.health import readiness_state

    try:
        instance = load_instance_manifest(settings.instance_manifest_path)
    except ValueError:
        instance = None
    ready, failure_code, capabilities = readiness_state(settings, release)
    counts = {
        table: _safe_table_count(settings.web.business_db_path, table)
        for table in (
            "users",
            "orders",
            "conversations",
            "agent_runs",
            "refund_actions",
            "l2_support_cases",
        )
    }
    counts.update(_operational_counts(settings.web.business_db_path))
    return RuntimeStatus(
        alive=True,
        ready=ready,
        release_version=release.app_version,
        release_commit=release.git_commit,
        instance_id=instance.instance_id if instance else None,
        lock_held=instance_lock_held(settings.instance_lock_path),
        capabilities=capabilities,
        counts=counts,
        failure_codes=(failure_code,) if failure_code is not None else (),
    )


def reset_derived_policy_index(settings: DeploymentSettings) -> None:
    """从版本化政策源重建派生索引，不读取 Backup 中的旧索引。"""

    build_policy_index(
        settings.web.policy_source_path,
        settings.web.policy_index_db_path,
    )


def clear_temporary_directory(settings: DeploymentSettings) -> None:
    """清理运维中断留下的临时目录，不触碰权威数据库和备份。"""

    temporary = settings.data_root / "tmp"
    if temporary.is_dir():
        shutil.rmtree(temporary)
