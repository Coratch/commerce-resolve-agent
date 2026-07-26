"""验证单机实例锁的独占、探测和幂等释放。"""

from pathlib import Path

import pytest

from commerce_resolve.operations.locking import (
    InstanceLock,
    InstanceLockUnavailable,
    instance_lock_held,
)


def test_instance_lock_rejects_second_writer(tmp_path: Path) -> None:
    """验证服务持锁期间初始化、备份和升级不能并发写实例。"""

    lock_path = tmp_path / ".instance.lock"
    first = InstanceLock(lock_path)
    first.acquire()
    try:
        assert instance_lock_held(lock_path) is True
        with pytest.raises(InstanceLockUnavailable):
            InstanceLock(lock_path).acquire()
    finally:
        first.release()

    assert instance_lock_held(lock_path) is False


def test_instance_lock_release_is_idempotent(tmp_path: Path) -> None:
    """验证异常收尾重复释放不会关闭其他资源或遗留占用。"""

    lock = InstanceLock(tmp_path / ".instance.lock")
    lock.acquire()
    lock.release()
    lock.release()
    with InstanceLock(lock.path):
        assert instance_lock_held(lock.path) is True


def test_lock_probe_does_not_modify_unlocked_file(tmp_path: Path) -> None:
    """验证只读状态检查不会改写既有锁文件内容或时间语义。"""

    lock_path = tmp_path / ".instance.lock"
    lock_path.write_text("sentinel\n", encoding="utf-8")

    assert instance_lock_held(lock_path) is False
    assert lock_path.read_text(encoding="utf-8") == "sentinel\n"
