"""使用固定命令、环境白名单和脱敏日志执行统一离线发布门禁。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from commerce_resolve.eval_models import EvalReleaseCheckResult
from commerce_resolve.eval_runtime import (
    compare_with_baseline,
    read_baseline,
    read_run_report,
)


@dataclass(frozen=True)
class ReleaseCheck:
    """声明一个不可由用户改写的固定工程检查。"""

    check_id: str
    argv: tuple[str, ...]
    cwd_kind: Literal["root", "frontend"] = "root"
    timeout_seconds: int = 300
    required: bool = True
    incomplete_exit_codes: tuple[int, ...] = ()
    allowed_environment_keys: tuple[str, ...] = (
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "CI",
        "CONDA_EXE",
        "CONDA_PREFIX",
    )


class ReleaseGateReport(BaseModel):
    """汇总固定工程检查与两次离线结果的一致性。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    status: Literal["passed", "failed", "incomplete"]
    checks: tuple[EvalReleaseCheckResult, ...]
    offline_result_fingerprint: str | None = None
    required_checks: int
    passed_checks: int


class ReleaseExecutor(Protocol):
    """定义可由 Fake 替换的固定检查执行接口。"""

    def execute(
        self,
        check: ReleaseCheck,
        *,
        project_root: Path,
        log_path: Path,
    ) -> EvalReleaseCheckResult:
        """执行检查并返回脱敏结构化结果。"""


def _allowed_environment(check: ReleaseCheck) -> dict[str, str]:
    """只复制显式允许的宿主变量，并永远排除 LLM 凭据。"""

    return {
        key: value
        for key in check.allowed_environment_keys
        if (value := os.environ.get(key)) is not None and not key.startswith("LLM_")
    }


def _redact_output(output: bytes) -> bytes:
    """脱敏日志中的 Token、私钥和用户绝对目录。"""

    replacements = (
        (re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"), b"[redacted-token]"),
        (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), b"[redacted-key]"),
        (re.compile(rb"/Users/[^/\s]+/"), b"/Users/[redacted]/"),
        (re.compile(rb"/home/[^/\s]+/"), b"/home/[redacted]/"),
    )
    result = output
    for pattern, replacement in replacements:
        result = pattern.sub(replacement, result)
    return result


class SubprocessReleaseExecutor:
    """使用 shell=False、进程组超时和环境白名单执行固定命令。"""

    def execute(
        self,
        check: ReleaseCheck,
        *,
        project_root: Path,
        log_path: Path,
    ) -> EvalReleaseCheckResult:
        """执行一个固定命令，超时时终止整个进程组并写脱敏日志。"""

        if not check.argv or any("\x00" in argument for argument in check.argv):
            raise ValueError("ReleaseCheck argv 非法")
        cwd = (
            project_root / "frontend" if check.cwd_kind == "frontend" else project_root
        )
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                check.argv,
                cwd=cwd,
                env=_allowed_environment(check),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            summary = f"executor_unavailable:{type(error).__name__}"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(summary, encoding="utf-8")
            return EvalReleaseCheckResult(
                check_id=check.check_id,
                status="incomplete",
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                output_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                summary=summary,
            )
        timed_out = False
        try:
            output, _ = process.communicate(timeout=check.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        redacted = _redact_output(output)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(redacted)
        digest = hashlib.sha256(redacted).hexdigest()
        tail = redacted[-1200:].decode("utf-8", errors="replace").strip()
        if timed_out:
            return EvalReleaseCheckResult(
                check_id=check.check_id,
                status="incomplete",
                exit_code=None,
                duration_ms=duration_ms,
                output_hash=digest,
                summary=f"timeout:{check.timeout_seconds}s; {tail}",
            )
        status = (
            "incomplete"
            if process.returncode in check.incomplete_exit_codes
            else "passed"
            if process.returncode == 0
            else "failed"
        )
        return EvalReleaseCheckResult(
            check_id=check.check_id,
            status=status,
            exit_code=process.returncode,
            duration_ms=duration_ms,
            output_hash=digest,
            summary=tail or None,
        )


def build_release_checks(
    project_root: Path, release_dir: Path
) -> tuple[ReleaseCheck, ...]:
    """构建固定且完整的后端、迁移、前端、E2E 和安全检查集合。"""

    python = sys.executable
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("Release Gate 需要 npm")
    offline_root = release_dir / "offline-runs"
    return (
        ReleaseCheck(
            "backend-tests", (python, "-m", "pytest", "-q"), timeout_seconds=600
        ),
        ReleaseCheck("backend-ruff-check", (python, "-m", "ruff", "check", ".")),
        ReleaseCheck(
            "backend-ruff-format", (python, "-m", "ruff", "format", "--check", ".")
        ),
        ReleaseCheck("python-dependency-check", (python, "-m", "pip", "check")),
        ReleaseCheck(
            "deployment-bundle",
            (python, "-m", "commerce_resolve.release_checks", "deployment-bundle"),
        ),
        ReleaseCheck(
            "deployment-operations-tests",
            (
                python,
                "-m",
                "pytest",
                "-q",
                "tests/test_operations_models.py",
                "tests/test_operations_preflight.py",
                "tests/test_operations_lifecycle.py",
                "tests/test_operations_health.py",
                "tests/test_operations_backup_restore.py",
                "tests/test_operations_upgrade.py",
                "tests/test_structured_logging.py",
                "tests/test_deployment_bundle.py",
                "tests/test_operations_evaluation.py",
            ),
            timeout_seconds=300,
        ),
        ReleaseCheck(
            "docker-compose-config",
            (python, "-m", "commerce_resolve.release_checks", "compose-config"),
            incomplete_exit_codes=(3,),
        ),
        ReleaseCheck(
            "offline-eval-repeat-1",
            (
                python,
                "-m",
                "commerce_resolve",
                "eval",
                "run",
                "--output-root",
                str(offline_root),
                "--run-id",
                "offline-repeat-1",
            ),
            timeout_seconds=300,
        ),
        ReleaseCheck(
            "offline-eval-repeat-2",
            (
                python,
                "-m",
                "commerce_resolve",
                "eval",
                "run",
                "--output-root",
                str(offline_root),
                "--run-id",
                "offline-repeat-2",
            ),
            timeout_seconds=300,
        ),
        ReleaseCheck(
            "empty-database-migration",
            (python, "-m", "commerce_resolve.release_checks", "empty-migration"),
        ),
        ReleaseCheck(
            "current-migration-head",
            (python, "-m", "commerce_resolve.release_checks", "current-head"),
        ),
        ReleaseCheck(
            "openapi-generated-types-consistency",
            (python, "-m", "commerce_resolve.release_checks", "openapi"),
            timeout_seconds=180,
        ),
        ReleaseCheck(
            "frontend-typecheck", (npm, "run", "typecheck"), cwd_kind="frontend"
        ),
        ReleaseCheck("frontend-unit-tests", (npm, "run", "test"), cwd_kind="frontend"),
        ReleaseCheck("frontend-build", (npm, "run", "build"), cwd_kind="frontend"),
        ReleaseCheck(
            "browser-e2e",
            (npm, "run", "test:e2e"),
            cwd_kind="frontend",
            timeout_seconds=300,
        ),
        ReleaseCheck(
            "browser-e2e-offline",
            (npm, "run", "test:e2e:offline"),
            cwd_kind="frontend",
            timeout_seconds=300,
        ),
        ReleaseCheck(
            "git-sensitive-artifact-check",
            (python, "-m", "commerce_resolve.release_checks", "sensitive"),
        ),
    )


def _repeatability_check(release_dir: Path) -> EvalReleaseCheckResult:
    """比较两次离线 Artifact 的规范化结果摘要。"""

    started = time.monotonic()
    try:
        first = read_run_report(release_dir / "offline-runs/offline-repeat-1")
        second = read_run_report(release_dir / "offline-runs/offline-repeat-2")
        passed = first.result_fingerprint == second.result_fingerprint
        summary = first.result_fingerprint if passed else "result_fingerprint_mismatch"
        return EvalReleaseCheckResult(
            check_id="offline-result-repeatability",
            status="passed" if passed else "failed",
            exit_code=0 if passed else 1,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            summary=summary,
        )
    except (OSError, ValueError) as error:
        return EvalReleaseCheckResult(
            check_id="offline-result-repeatability",
            status="incomplete",
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            summary=type(error).__name__,
        )


def _baseline_comparison_check(
    release_dir: Path,
    baseline_path: Path | None,
) -> EvalReleaseCheckResult:
    """比较首个离线 Candidate 与显式接受的 Baseline。"""

    started = time.monotonic()
    if baseline_path is None:
        return EvalReleaseCheckResult(
            check_id="offline-baseline-comparison",
            status="incomplete",
            duration_ms=0,
            summary="baseline_required",
        )
    try:
        candidate = read_run_report(release_dir / "offline-runs/offline-repeat-1")
        comparison = compare_with_baseline(candidate, read_baseline(baseline_path))
        summary = f"{comparison.status}:{comparison.baseline_id}"
        return EvalReleaseCheckResult(
            check_id="offline-baseline-comparison",
            status="passed" if comparison.status == "passed" else "failed",
            exit_code=0 if comparison.status == "passed" else 1,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            summary=summary,
        )
    except (OSError, ValueError) as error:
        return EvalReleaseCheckResult(
            check_id="offline-baseline-comparison",
            status="incomplete",
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            summary=type(error).__name__,
        )


def run_release_gate(
    project_root: Path,
    output_root: Path,
    *,
    executor: ReleaseExecutor | None = None,
    checks: Sequence[ReleaseCheck] | None = None,
    run_id: str | None = None,
    baseline_path: Path | None = None,
) -> tuple[ReleaseGateReport, Path]:
    """顺序执行固定门禁，输出结构化报告并在失败时继续收集证据。"""

    release_run_id = run_id or f"release-{uuid4().hex[:12]}"
    release_dir = output_root / release_run_id
    if release_dir.exists():
        raise FileExistsError(f"Release Artifact 已存在：{release_run_id}")
    release_dir.mkdir(parents=True)
    logs = release_dir / "logs"
    selected = tuple(checks or build_release_checks(project_root, release_dir))
    command_executor = executor or SubprocessReleaseExecutor()
    results = [
        command_executor.execute(
            check,
            project_root=project_root,
            log_path=logs / f"{check.check_id}.log",
        )
        for check in selected
    ]
    check_ids = {check.check_id for check in selected}
    fingerprint: str | None = None
    repeatability_required = {
        "offline-eval-repeat-1",
        "offline-eval-repeat-2",
    }.issubset(check_ids)
    if repeatability_required:
        repeatability = _repeatability_check(release_dir)
        results.append(repeatability)
        results.append(_baseline_comparison_check(release_dir, baseline_path))
        if repeatability.status == "passed":
            fingerprint = repeatability.summary
    required_ids = {check.check_id for check in selected if check.required}
    if repeatability_required:
        required_ids.add("offline-result-repeatability")
        required_ids.add("offline-baseline-comparison")
    required_results = [item for item in results if item.check_id in required_ids]
    status = (
        "incomplete"
        if any(item.status == "incomplete" for item in required_results)
        else "passed"
        if all(item.status == "passed" for item in required_results)
        else "failed"
    )
    report = ReleaseGateReport(
        run_id=release_run_id,
        status=status,
        checks=tuple(results),
        offline_result_fingerprint=fingerprint,
        required_checks=len(required_results),
        passed_checks=sum(item.status == "passed" for item in required_results),
    )
    payload = report.model_dump(mode="json")
    (release_dir / "release.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (release_dir / "report.md").write_text(
        "\n".join(
            (
                f"# Release Gate {release_run_id}",
                "",
                f"- Status: `{status}`",
                f"- Checks: `{report.passed_checks}/{report.required_checks}`",
                f"- Offline fingerprint: `{fingerprint or 'unavailable'}`",
                "",
            )
        ),
        encoding="utf-8",
    )
    return report, release_dir
