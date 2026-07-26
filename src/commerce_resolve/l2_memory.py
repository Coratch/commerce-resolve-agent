"""使用独立 LangGraph Store 管理用户明确确认的低风险长期偏好。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore

from commerce_resolve.l2_models import (
    CustomerPreference,
    MemoryProposal,
    MemoryType,
    MemoryValue,
)

MEMORY_NAMESPACE_ROOT = "commerce-resolve"


def _utc_now() -> datetime:
    """返回当前 UTC 时间，供长期偏好确认和纠正使用。"""

    return datetime.now(UTC)


def preference_namespace(
    *,
    user_id: str,
    workspace_id: str,
) -> tuple[str, ...]:
    """根据服务端可信身份生成用户无法覆盖的 Store namespace。"""

    if not user_id or not workspace_id:
        raise ValueError("memory namespace requires user and workspace")
    return (
        MEMORY_NAMESPACE_ROOT,
        "workspace",
        workspace_id,
        "user",
        user_id,
        "preferences",
    )


@contextmanager
def open_sqlite_memory_store(database_path: str | Path) -> Iterator[SqliteStore]:
    """打开独立 SQLite Store，调用方负责确保已经显式执行 setup。"""

    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with SqliteStore.from_conn_string(str(database)) as store:
        yield store


def setup_memory_store(database_path: str | Path) -> None:
    """显式创建 LangGraph SQLite Store Schema，不写入任何用户偏好。"""

    with open_sqlite_memory_store(database_path) as store:
        store.setup()


def assert_memory_store_ready(database_path: str | Path) -> None:
    """验证 Memory Store 已初始化；缺失时返回稳定启动错误。"""

    try:
        with open_sqlite_memory_store(database_path) as store:
            store.search((MEMORY_NAMESPACE_ROOT,), limit=1)
    except Exception as error:
        raise RuntimeError("长期记忆 Store 未初始化，请先执行 memory setup") from error


def list_preferences(
    store: BaseStore,
    *,
    user_id: str,
    workspace_id: str,
) -> tuple[CustomerPreference, ...]:
    """列出当前可信 namespace 中全部且仅限合法 Schema 的偏好。"""

    namespace = preference_namespace(user_id=user_id, workspace_id=workspace_id)
    preferences = tuple(
        CustomerPreference.model_validate(item.value)
        for item in store.search(namespace, limit=3)
    )
    return tuple(sorted(preferences, key=lambda item: item.memory_type))


def get_preference(
    store: BaseStore,
    *,
    user_id: str,
    workspace_id: str,
    memory_id: str,
) -> CustomerPreference | None:
    """按公开稳定标识读取本人偏好，不泄露其他 namespace 的存在性。"""

    return next(
        (
            item
            for item in list_preferences(
                store,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            if item.memory_id == memory_id
        ),
        None,
    )


def confirm_preference(
    store: BaseStore,
    *,
    user_id: str,
    workspace_id: str,
    proposal: MemoryProposal,
    now: datetime | None = None,
) -> CustomerPreference:
    """幂等保存用户已确认的偏好，并回读验证实际 Store 结果。"""

    confirmed_at = now or _utc_now()
    namespace = preference_namespace(user_id=user_id, workspace_id=workspace_id)
    existing_item = store.get(namespace, proposal.memory_type)
    existing = (
        CustomerPreference.model_validate(existing_item.value)
        if existing_item is not None
        else None
    )
    preference = CustomerPreference(
        memory_id=existing.memory_id if existing is not None else str(uuid4()),
        memory_type=proposal.memory_type,
        value=proposal.value,
        source_case_id=proposal.case_id,
        created_at=existing.created_at if existing is not None else confirmed_at,
        last_confirmed_at=confirmed_at,
    )
    store.put(
        namespace,
        proposal.memory_type,
        preference.model_dump(mode="json"),
        index=False,
    )
    saved = store.get(namespace, proposal.memory_type)
    if saved is None:
        raise RuntimeError("长期偏好写入后无法验证")
    return CustomerPreference.model_validate(saved.value)


def correct_preference(
    store: BaseStore,
    *,
    user_id: str,
    workspace_id: str,
    memory_id: str,
    value: MemoryValue,
    now: datetime | None = None,
) -> CustomerPreference | None:
    """纠正本人既有偏好；值不匹配原类型时由领域 Schema 拒绝。"""

    existing = get_preference(
        store,
        user_id=user_id,
        workspace_id=workspace_id,
        memory_id=memory_id,
    )
    if existing is None:
        return None
    corrected = CustomerPreference(
        memory_id=existing.memory_id,
        memory_type=existing.memory_type,
        value=value,
        source_case_id=existing.source_case_id,
        created_at=existing.created_at,
        last_confirmed_at=now or _utc_now(),
    )
    namespace = preference_namespace(user_id=user_id, workspace_id=workspace_id)
    store.put(
        namespace,
        existing.memory_type,
        corrected.model_dump(mode="json"),
        index=False,
    )
    return corrected


def delete_preference(
    store: BaseStore,
    *,
    user_id: str,
    workspace_id: str,
    memory_id: str,
) -> bool:
    """幂等删除本人偏好；不存在或不属于当前 namespace 时返回 False。"""

    existing = get_preference(
        store,
        user_id=user_id,
        workspace_id=workspace_id,
        memory_id=memory_id,
    )
    if existing is None:
        return False
    namespace = preference_namespace(user_id=user_id, workspace_id=workspace_id)
    store.delete(namespace, existing.memory_type)
    return True


def clear_preferences(
    store: BaseStore,
    *,
    user_id: str,
    workspace_id: str,
) -> int:
    """删除目标工作区内全部已确认偏好，并返回实际删除数量。"""

    preferences = list_preferences(
        store,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    namespace = preference_namespace(user_id=user_id, workspace_id=workspace_id)
    for preference in preferences:
        store.delete(namespace, preference.memory_type)
    return len(preferences)


def validate_memory_value(memory_type: MemoryType, value: MemoryValue) -> None:
    """在 Web 更新前验证值属于指定偏好类型，不产生 Store 副作用。"""

    now = _utc_now()
    CustomerPreference(
        memory_id="validation",
        memory_type=memory_type,
        value=value,
        source_case_id="validation",
        created_at=now,
        last_confirmed_at=now,
    )
