"""创建、读取并原子写入 Release 与 Instance Manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from commerce_resolve import __version__
from commerce_resolve.adapters.sqlite_business import business_schema_head
from commerce_resolve.adapters.sqlite_policy import calculate_policy_corpus_hash

from .models import InstanceManifest, ReleaseManifest


def sha256_file(path: str | Path) -> str:
    """流式计算一个文件的 SHA-256，不把大文件整体读入内存。"""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(root: str | Path) -> str:
    """按相对路径排序计算目录内容摘要，并忽略目录时间戳。"""

    base = Path(root)
    digest = hashlib.sha256()
    if not base.is_dir():
        return "missing"
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def atomic_write_json(path: str | Path, payload: dict[str, object]) -> None:
    """在目标目录写临时文件、刷新磁盘后原子替换旧 JSON。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def load_release_manifest(path: str | Path) -> ReleaseManifest:
    """读取并严格校验发布清单，拒绝未知字段和不兼容 Schema。"""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("release_manifest_unreadable") from error
    manifest = ReleaseManifest.model_validate(payload)
    if manifest.schema_version != "release-manifest-v1":
        raise ValueError("release_manifest_schema_unsupported")
    return manifest


def load_instance_manifest(path: str | Path) -> InstanceManifest:
    """读取并严格校验实例清单，不根据缺失文件隐式创建实例。"""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("instance_manifest_unreadable") from error
    manifest = InstanceManifest.model_validate(payload)
    if manifest.schema_version != "instance-manifest-v1":
        raise ValueError("instance_manifest_schema_unsupported")
    return manifest


def write_instance_manifest(path: str | Path, manifest: InstanceManifest) -> None:
    """原子保存实例身份；调用方必须先完成全部数据验证。"""

    atomic_write_json(path, manifest.model_dump(mode="json"))


def new_instance_manifest(
    release: ReleaseManifest,
    *,
    source_version: str = "new",
    restored_from: str | None = None,
) -> InstanceManifest:
    """为成功初始化的空实例生成不可复用的稳定身份。"""

    return InstanceManifest(
        instance_id=str(uuid4()),
        initialized_at=datetime.now(UTC),
        last_successful_release=release.app_version,
        last_successful_commit=release.git_commit,
        source_version=source_version,
        restored_from=restored_from,
    )


def build_release_manifest(
    *,
    project_root: Path,
    app_version: str,
    git_commit: str,
    build_timestamp: datetime,
    frontend_dist: Path,
    baseline_id: str,
) -> ReleaseManifest:
    """从锁文件、前端产物、政策语料和迁移 Head 生成发布清单。"""

    frontend_package = json.loads(
        (project_root / "frontend/package.json").read_text(encoding="utf-8")
    )
    return ReleaseManifest(
        app_version=app_version,
        git_commit=git_commit,
        build_timestamp=build_timestamp,
        python_version=".".join(str(item) for item in sys.version_info[:3]),
        frontend_version=str(frontend_package["version"]),
        frontend_asset_hash=sha256_tree(frontend_dist),
        runtime_lock_hash=sha256_file(project_root / "requirements.runtime.lock"),
        npm_lock_hash=sha256_file(project_root / "frontend/package-lock.json"),
        policy_source_hash=calculate_policy_corpus_hash(project_root / "data/policies"),
        business_schema_head=business_schema_head(),
        offline_baseline_id=baseline_id,
    )


def development_release_manifest(project_root: Path) -> ReleaseManifest:
    """为非部署测试生成明确标记的开发清单，不冒充正式 Bundle。"""

    runtime_lock = project_root / "requirements.runtime.lock"
    frontend_package = json.loads(
        (project_root / "frontend/package.json").read_text(encoding="utf-8")
    )
    return ReleaseManifest(
        app_version=__version__,
        git_commit="development",
        build_timestamp=datetime.now(UTC),
        python_version=".".join(str(item) for item in sys.version_info[:3]),
        frontend_version=str(frontend_package["version"]),
        frontend_asset_hash=sha256_tree(project_root / "frontend/dist"),
        runtime_lock_hash=(
            sha256_file(runtime_lock) if runtime_lock.is_file() else "missing"
        ),
        npm_lock_hash=sha256_file(project_root / "frontend/package-lock.json"),
        policy_source_hash=calculate_policy_corpus_hash(project_root / "data/policies"),
        business_schema_head=business_schema_head(),
        offline_baseline_id="development",
    )


def _build_parser() -> argparse.ArgumentParser:
    """构造仅供 Docker Builder 调用的发布清单生成参数。"""

    parser = argparse.ArgumentParser(prog="commerce-resolve-release-manifest")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--build-timestamp", required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    """生成镜像只读发布清单，并在版本或构建输入不合法时失败。"""

    args = _build_parser().parse_args()
    timestamp = datetime.fromisoformat(args.build_timestamp.replace("Z", "+00:00"))
    manifest = build_release_manifest(
        project_root=args.project_root,
        app_version=args.app_version,
        git_commit=args.git_commit,
        build_timestamp=timestamp,
        frontend_dist=args.frontend_dist,
        baseline_id=args.baseline_id,
    )
    if manifest.app_version != __version__:
        raise ValueError("app_version_mismatch")
    if manifest.frontend_version != manifest.app_version:
        raise ValueError("frontend_version_mismatch")
    atomic_write_json(args.output, manifest.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
