"""验证长期偏好 Store 的确认、隔离、纠正、删除和跨实例恢复。"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from commerce_resolve.l2_memory import (
    confirm_preference,
    correct_preference,
    delete_preference,
    list_preferences,
    open_sqlite_memory_store,
    setup_memory_store,
)
from commerce_resolve.l2_models import MemoryProposal

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _proposal() -> MemoryProposal:
    """返回固定测试 Case 提出的中文偏好建议。"""

    return MemoryProposal(
        proposal_id="proposal-001",
        case_id="case-001",
        memory_type="preferred_language",
        value="zh-CN",
        purpose="后续客服使用该语言回复",
    )


def test_unconfirmed_proposal_does_not_write_memory() -> None:
    """验证只创建建议不会隐式写入任何长期偏好。"""

    store = InMemoryStore()
    _proposal()

    assert list_preferences(store, user_id="u1", workspace_id="w1") == ()


def test_confirm_correct_and_delete_are_scoped_and_schema_checked() -> None:
    """验证确认后可纠正删除，其他用户始终不可见。"""

    store = InMemoryStore()
    saved = confirm_preference(
        store,
        user_id="u1",
        workspace_id="w1",
        proposal=_proposal(),
        now=NOW,
    )

    assert saved.value == "zh-CN"
    assert len(list_preferences(store, user_id="u1", workspace_id="w1")) == 1
    assert list_preferences(store, user_id="u2", workspace_id="w2") == ()

    corrected = correct_preference(
        store,
        user_id="u1",
        workspace_id="w1",
        memory_id=saved.memory_id,
        value="en",
        now=NOW,
    )
    assert corrected is not None and corrected.value == "en"

    with pytest.raises(ValidationError):
        correct_preference(
            store,
            user_id="u1",
            workspace_id="w1",
            memory_id=saved.memory_id,
            value="friendly",
            now=NOW,
        )

    assert (
        delete_preference(
            store,
            user_id="u2",
            workspace_id="w2",
            memory_id=saved.memory_id,
        )
        is False
    )
    assert (
        delete_preference(
            store,
            user_id="u1",
            workspace_id="w1",
            memory_id=saved.memory_id,
        )
        is True
    )
    assert list_preferences(store, user_id="u1", workspace_id="w1") == ()


def test_sqlite_store_restores_confirmed_memory_across_instances(
    tmp_path: Path,
) -> None:
    """验证关闭连接后仍能从独立 SQLite Store 恢复确认偏好。"""

    database = tmp_path / "memory.sqlite"
    setup_memory_store(database)
    with open_sqlite_memory_store(database) as first:
        saved = confirm_preference(
            first,
            user_id="u1",
            workspace_id="w1",
            proposal=_proposal(),
            now=NOW,
        )

    with open_sqlite_memory_store(database) as second:
        restored = list_preferences(second, user_id="u1", workspace_id="w1")

    assert len(restored) == 1
    assert restored[0].memory_id == saved.memory_id
    assert restored[0].value == "zh-CN"
