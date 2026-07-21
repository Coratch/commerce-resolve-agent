"""提供发布门禁使用的迁移、OpenAPI 和敏感产物确定性检查。"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from commerce_resolve.adapters.sqlite_business import (
    assert_business_schema_current,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.cli import _run_openapi_export


def check_empty_database_migration() -> None:
    """从空 SQLite 文件升级到当前 Head，并验证 Schema 可启动。"""

    with TemporaryDirectory() as directory:
        database = Path(directory) / "business.sqlite"
        upgrade_business_database(database)
        engine = create_business_engine(database)
        try:
            assert_business_schema_current(engine, database)
        finally:
            engine.dispose()


def check_v07_migration_head(project_root: Path) -> None:
    """确认 v0.8 未偷偷新增业务迁移，当前 Head 仍是已接受的 v0.7。"""

    versions = sorted((project_root / "migrations/versions").glob("*.py"))
    if not versions or not versions[-1].name.startswith("20260721_0005_"):
        raise RuntimeError("v0.8 业务迁移 Head 与 Plan 不一致")
    check_empty_database_migration()


def check_openapi_generated_types(project_root: Path) -> None:
    """在临时目录重新生成 OpenAPI TypeScript，并与提交版本逐字比较。"""

    executable = project_root / "frontend/node_modules/.bin/openapi-typescript"
    if not executable.is_file():
        raise RuntimeError("缺少 frontend/node_modules，请先执行 npm ci")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "business.sqlite"
        upgrade_business_database(database)
        openapi = root / "openapi.json"
        exit_code = _run_openapi_export(
            openapi,
            business_database=database,
            checkpoint_database=root / "checkpoints.sqlite",
            policy_source=project_root / "data/policies",
            policy_database=root / "policy.sqlite",
            memory_database=root / "memory.sqlite",
        )
        if exit_code != 0:
            raise RuntimeError("OpenAPI 导出失败")
        generated = root / "generated.ts"
        subprocess.run(
            (str(executable), str(openapi), "-o", str(generated)),
            cwd=project_root / "frontend",
            check=True,
            capture_output=True,
            timeout=60,
        )
        committed = project_root / "frontend/src/api/generated.ts"
        if generated.read_bytes() != committed.read_bytes():
            raise RuntimeError("OpenAPI 生成类型与提交版本不一致")


def _candidate_files(project_root: Path) -> tuple[Path, ...]:
    """返回 Git 已跟踪及待提交且未被忽略的候选文件。"""

    output = subprocess.run(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        cwd=project_root,
        check=True,
        capture_output=True,
        timeout=10,
    ).stdout
    return tuple(
        project_root / item.decode("utf-8") for item in output.split(b"\0") if item
    )


def check_sensitive_artifacts(project_root: Path) -> None:
    """拒绝待提交数据库、密钥文件、运行产物和高置信凭据。"""

    forbidden_names = {".env", "id_rsa", "id_ed25519"}
    forbidden_parts = {"var", "test-results", "playwright-report", "__pycache__"}
    forbidden_suffixes = {".sqlite", ".db", ".log", ".pem", ".key"}
    content_patterns = (
        re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(rb"LLM_API_KEY[ \t]*=[ \t]*[^\s#][^\r\n]*"),
    )
    failures: list[str] = []
    for path in _candidate_files(project_root):
        relative = path.relative_to(project_root)
        if (
            path.name in forbidden_names
            or forbidden_parts.intersection(relative.parts)
            or path.suffix.lower() in forbidden_suffixes
        ):
            failures.append(f"forbidden-file:{relative.as_posix()}")
            continue
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        content = path.read_bytes()
        if any(pattern.search(content) for pattern in content_patterns):
            failures.append(f"credential-pattern:{relative.as_posix()}")
    if failures:
        raise RuntimeError("；".join(sorted(failures)))


def build_parser() -> argparse.ArgumentParser:
    """构造只允许三种固定检查的内部 CLI。"""

    parser = argparse.ArgumentParser(prog="commerce-resolve-release-check")
    parser.add_argument(
        "check", choices=("empty-migration", "v07-head", "openapi", "sensitive")
    )
    return parser


def main() -> int:
    """执行一个固定内部检查，并以退出码提供门禁事实。"""

    args = build_parser().parse_args()
    project_root = Path.cwd()
    if args.check == "empty-migration":
        check_empty_database_migration()
    elif args.check == "v07-head":
        check_v07_migration_head(project_root)
    elif args.check == "openapi":
        check_openapi_generated_types(project_root)
    else:
        check_sensitive_artifacts(project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
