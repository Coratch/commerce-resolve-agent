"""使用 POSIX flock 提供单机实例互斥。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class InstanceLockUnavailable(RuntimeError):
    """表示另一个服务或运维进程正在持有同一实例锁。"""


class InstanceLock:
    """在对象生命周期内持有非阻塞 POSIX 独占文件锁。"""

    def __init__(self, path: str | Path) -> None:
        """保存锁路径；构造阶段不创建文件也不取得锁。"""

        self.path = Path(path)
        self._descriptor: int | None = None

    def acquire(self) -> None:
        """创建锁文件并立即尝试独占，冲突时不等待。"""

        if self._descriptor is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise InstanceLockUnavailable("instance_lock_held") from error
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        self._descriptor = descriptor

    def release(self) -> None:
        """释放当前进程持有的锁；重复调用保持幂等。"""

        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> InstanceLock:
        """进入上下文时取得实例锁。"""

        self.acquire()
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """离开上下文时可靠释放实例锁。"""

        self.release()


def instance_lock_held(path: str | Path) -> bool:
    """无写入探测实例锁是否被占用，并立即释放临时文件描述符。"""

    target = Path(path)
    if not target.exists():
        return False
    try:
        descriptor = os.open(target, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)
