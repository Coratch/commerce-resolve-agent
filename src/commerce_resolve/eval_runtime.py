"""执行统一离线 Eval，并管理可复现 Artifact、Baseline 与比较。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from commerce_resolve.eval_catalog import (
    EvalSuiteAdapter,
    build_eval_catalog,
    content_hash,
    registered_adapters,
)
from commerce_resolve.eval_models import (
    EvalBaseline,
    EvalComparison,
    EvalComparisonItem,
    EvalRunManifest,
    EvalRunReport,
    EvalSuiteOutcome,
    MetricScalar,
)

EVAL_SCHEMA_VERSION = "1.0"
OFFLINE_PROFILE_ID = "offline-release"
OFFLINE_PROFILE_VERSION = "v0.8.0"
DEFAULT_RUN_ROOT = Path("var/eval/runs")
SOURCE_PATHS = (
    "src",
    "tests",
    "frontend/src",
    "frontend/e2e",
    "migrations",
    "data/policies",
    "data/eval",
    "pyproject.toml",
    "requirements.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
)
EXCLUDED_SOURCE_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "baselines",
    "node_modules",
    "dist",
    "var",
}
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "password",
    "secret",
    "cookie",
    "authorization",
    "base_url",
    "raw_prompt",
    "raw_response",
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/home/[^/\s]+/"),
)


def _sha256_bytes(value: bytes) -> str:
    """计算原始字节的 SHA-256 摘要。"""

    return hashlib.sha256(value).hexdigest()


def _iter_source_files(project_root: Path) -> tuple[Path, ...]:
    """按稳定顺序返回参与 Source Fingerprint 的项目文件。"""

    files: list[Path] = []
    for relative in SOURCE_PATHS:
        path = project_root / relative
        if path.is_file():
            files.append(path)
            continue
        if not path.is_dir():
            continue
        for candidate in path.rglob("*"):
            relative_parts = candidate.relative_to(project_root).parts
            if candidate.is_file() and not EXCLUDED_SOURCE_PARTS.intersection(
                relative_parts
            ):
                files.append(candidate)
    return tuple(
        sorted(
            set(files),
            key=lambda item: item.relative_to(project_root).as_posix(),
        )
    )


def source_fingerprint(project_root: Path) -> str:
    """根据相对路径和文件内容计算稳定源码摘要。"""

    digest = hashlib.sha256()
    for path in _iter_source_files(project_root):
        relative = path.relative_to(project_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def dependency_fingerprints(project_root: Path) -> dict[str, str]:
    """计算后端和前端锁文件摘要，不保存本机路径。"""

    result: dict[str, str] = {}
    for relative in ("requirements.lock", "frontend/package-lock.json"):
        path = project_root / relative
        if path.is_file():
            result[relative] = _sha256_bytes(path.read_bytes())
    return result


def _git_state(project_root: Path) -> tuple[str | None, bool]:
    """读取 Git Commit 和 Dirty 状态，失败时退化为无 Commit。"""

    try:
        commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        return commit or None, bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return None, True


def _application_version(project_root: Path) -> str:
    """从 pyproject 读取公开应用版本。"""

    data = tomllib.loads((project_root / "pyproject.toml").read_text("utf-8"))
    return str(data["project"]["version"])


def _node_version() -> str | None:
    """读取 Node.js 版本；未安装时返回空值。"""

    try:
        return subprocess.run(
            ("node", "--version"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _migration_head(project_root: Path) -> str:
    """从迁移文件名提取当前稳定 Alembic Head。"""

    versions = sorted((project_root / "migrations/versions").glob("*.py"))
    if not versions:
        return "none"
    name_parts = versions[-1].name.split("_", maxsplit=2)
    return f"{name_parts[0]}_{name_parts[1]}"


def _threshold_fingerprint(adapters: Sequence[EvalSuiteAdapter]) -> str:
    """计算当前全部指标门槛的规范化摘要。"""

    payload = [
        definition.model_dump(mode="json")
        for adapter in adapters
        for definition in adapter.metric_definitions
    ]
    return content_hash(payload)


def _fixture_fingerprint(adapters: Sequence[EvalSuiteAdapter]) -> str:
    """根据版本化场景描述计算 Fixture 摘要。"""

    payload = [
        {
            "scenario_id": scenario.scenario_id,
            "fixture_refs": scenario.fixture_refs,
            "descriptor_hash": scenario.descriptor_hash,
        }
        for adapter in adapters
        for scenario in adapter.descriptor().scenarios
    ]
    return content_hash(payload)


def build_run_manifest(
    project_root: Path,
    adapters: Sequence[EvalSuiteAdapter],
    *,
    run_id: str | None = None,
    started_at: datetime | None = None,
) -> EvalRunManifest:
    """构建不含密钥、Provider 地址和绝对路径的离线 Run Manifest。"""

    catalog = build_eval_catalog(adapters)
    git_commit, git_dirty = _git_state(project_root)
    return EvalRunManifest(
        schema_version=EVAL_SCHEMA_VERSION,
        run_id=run_id or f"offline-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}",
        channel="offline",
        profile_id=OFFLINE_PROFILE_ID,
        profile_version=OFFLINE_PROFILE_VERSION,
        started_at=started_at or datetime.now(UTC),
        git_commit=git_commit,
        git_dirty=git_dirty,
        source_fingerprint=source_fingerprint(project_root),
        catalog_version=catalog.catalog_version,
        catalog_fingerprint=catalog.fingerprint,
        fixture_fingerprint=_fixture_fingerprint(adapters),
        application_version=_application_version(project_root),
        python_version=platform.python_version(),
        node_version=_node_version(),
        dependency_fingerprints=dependency_fingerprints(project_root),
        model_provider="fake",
        model_name="deterministic-fakes",
        prompt_version="offline-fixtures-v0.8",
        schema_contract_version=EVAL_SCHEMA_VERSION,
        toolset_version="offline-tools-v0.8",
        policy_version="accepted-product-policies-v0.7",
        context_version="context-policy-v0.7",
        migration_head=_migration_head(project_root),
        threshold_fingerprint=_threshold_fingerprint(adapters),
    )


def _stable_scenario_payload(suite: EvalSuiteOutcome) -> list[dict[str, Any]]:
    """投影场景的确定性结果，排除耗时、Token 和本地运行信息。"""

    volatile_fragments = ("latency", "duration", "token", "cost", "elapsed")
    payload: list[dict[str, Any]] = []
    for scenario in suite.scenarios:
        metrics = {
            key: value
            for key, value in scenario.metrics.items()
            if not any(fragment in key.lower() for fragment in volatile_fragments)
        }
        payload.append(
            {
                "scenario_id": scenario.scenario_id,
                "passed": scenario.passed,
                "expected_terminal": scenario.expected_terminal,
                "actual_terminal": scenario.actual_terminal,
                "metrics": metrics,
                "safety_violations": scenario.safety_violations,
                "failure_layer": scenario.failure_layer,
                "failure_code": scenario.failure_code,
            }
        )
    return payload


def result_fingerprint(suites: Sequence[EvalSuiteOutcome]) -> str:
    """计算排除运行 ID、时间和效率抖动后的业务结果摘要。"""

    payload = [
        {
            "suite_id": suite.suite_id,
            "suite_version": suite.suite_version,
            "passed": suite.passed,
            "scenarios": _stable_scenario_payload(suite),
            "safety_violations": suite.safety_violations,
        }
        for suite in suites
    ]
    return content_hash(payload)


def _aggregate_metrics(suites: Sequence[EvalSuiteOutcome]) -> dict[str, MetricScalar]:
    """汇总通用场景数量和安全计数，不用平均分掩盖失败。"""

    total = sum(suite.total_scenarios for suite in suites)
    passed = sum(suite.passed_scenarios for suite in suites)
    violations = sum(len(suite.safety_violations) for suite in suites)
    return {
        "suite_total": len(suites),
        "scenario_total": total,
        "scenario_passed": passed,
        "scenario_pass_rate": passed / total if total else None,
        "safety_violation_count": violations,
    }


def run_offline_evaluation(
    project_root: Path,
    *,
    suite_versions: Iterable[str] | None = None,
    run_id: str | None = None,
) -> EvalRunReport:
    """按显式稳定顺序运行统一离线 Suite，并返回规范化报告。"""

    available = registered_adapters()
    requested = tuple(suite_versions or ("all",))
    if requested == ("all",):
        selected = available
    else:
        selected = tuple(
            adapter for adapter in available if adapter.suite_version in requested
        )
        if {adapter.suite_version for adapter in selected} != set(requested):
            raise ValueError("包含未知或重复的 Eval Suite")
    manifest = build_run_manifest(project_root, selected, run_id=run_id)
    suites = tuple(adapter.run() for adapter in selected)
    violations = tuple(
        violation for suite in suites for violation in suite.safety_violations
    )
    status = (
        "passed"
        if suites and all(suite.passed for suite in suites) and not violations
        else "failed"
    )
    completed_manifest = manifest.model_copy(update={"completed_at": datetime.now(UTC)})
    return EvalRunReport(
        manifest=completed_manifest,
        status=status,
        suites=suites,
        aggregate_metrics=_aggregate_metrics(suites),
        safety_violations=violations,
        result_fingerprint=result_fingerprint(suites),
    )


def _atomic_write(path: Path, content: bytes) -> None:
    """在同一目录写临时文件后原子替换目标文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _report_markdown(report: EvalRunReport) -> str:
    """只根据已校验 JSON 模型生成稳定 Markdown 摘要。"""

    lines = [
        f"# Eval Run {report.manifest.run_id}",
        "",
        f"- Status: `{report.status}`",
        f"- Channel: `{report.manifest.channel}`",
        f"- Profile: `{report.manifest.profile_id}`",
        f"- Result fingerprint: `{report.result_fingerprint}`",
        f"- Safety violations: `{len(report.safety_violations)}`",
        "",
        "| Suite | Passed | Scenarios |",
        "|---|---:|---:|",
    ]
    lines.extend(
        "| "
        f"`{suite.suite_id}` | "
        f"{suite.passed_scenarios}/{suite.total_scenarios} | "
        f"{suite.total_scenarios} |"
        for suite in report.suites
    )
    return "\n".join(lines) + "\n"


def assert_no_sensitive_artifact(value: object) -> None:
    """拒绝包含密钥类字段、私钥、Token 或用户绝对目录的 Artifact。"""

    def inspect(item: object, location: str) -> None:
        """递归检查 JSON 兼容值并仅报告字段位置。"""

        if isinstance(item, Mapping):
            for key, child in item.items():
                lowered = str(key).lower()
                if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                    raise ValueError(f"Artifact 包含禁止字段：{location}.{key}")
                inspect(child, f"{location}.{key}")
            return
        if isinstance(item, Sequence) and not isinstance(item, str | bytes):
            for index, child in enumerate(item):
                inspect(child, f"{location}[{index}]")
            return
        if isinstance(item, str) and any(
            pattern.search(item) for pattern in SENSITIVE_VALUE_PATTERNS
        ):
            raise ValueError(f"Artifact 包含敏感值：{location}")

    inspect(value, "$")


def write_run_artifact(
    report: EvalRunReport,
    output_root: Path,
) -> Path:
    """脱敏检查后原子写入 Manifest、Result 和 Markdown。"""

    payload = report.model_dump(mode="json")
    assert_no_sensitive_artifact(payload)
    run_dir = output_root / report.manifest.run_id
    if run_dir.exists():
        raise FileExistsError(f"Run Artifact 已存在：{report.manifest.run_id}")
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    try:
        _atomic_write(
            run_dir / "manifest.json",
            json.dumps(
                report.manifest.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8"),
        )
        _atomic_write(
            run_dir / "results.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode(
                "utf-8"
            ),
        )
        _atomic_write(run_dir / "report.md", _report_markdown(report).encode("utf-8"))
    except Exception:
        for child in sorted(run_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        run_dir.rmdir()
        raise
    return run_dir


def read_run_report(path: Path) -> EvalRunReport:
    """从 Run 目录或 results.json 读取并严格校验报告。"""

    results_path = path / "results.json" if path.is_dir() else path
    report = EvalRunReport.model_validate_json(results_path.read_text("utf-8"))
    assert_no_sensitive_artifact(report.model_dump(mode="json"))
    return report


def compare_with_baseline(
    candidate: EvalRunReport,
    baseline: EvalBaseline,
) -> EvalComparison:
    """按全局场景 ID 比较 Candidate，区分回归与不可比较。"""

    reasons: list[str] = []
    if candidate.manifest.channel != baseline.channel:
        reasons.append("channel 不一致")
    if candidate.manifest.profile_id != baseline.profile_id:
        reasons.append("profile 不一致")
    candidate_schema_major = candidate.manifest.schema_version.split(".", maxsplit=1)[0]
    baseline_schema_major = baseline.schema_version.split(".", maxsplit=1)[0]
    if candidate_schema_major != baseline_schema_major:
        reasons.append("schema major version 不一致")

    baseline_scenarios = {
        scenario.scenario_id: scenario
        for suite in baseline.suites
        for scenario in suite.scenarios
    }
    candidate_scenarios = {
        scenario.scenario_id: scenario
        for suite in candidate.suites
        for scenario in suite.scenarios
    }
    changes: list[EvalComparisonItem] = []
    for scenario_id in sorted(baseline_scenarios.keys() | candidate_scenarios.keys()):
        before = baseline_scenarios.get(scenario_id)
        after = candidate_scenarios.get(scenario_id)
        if before is None and after is not None:
            changes.append(
                EvalComparisonItem(
                    item_id=scenario_id,
                    change="new" if after.passed else "regression",
                    blocking=not after.passed,
                    candidate_value=after.passed,
                    reason=None if after.passed else "新增场景失败",
                )
            )
        elif before is not None and after is None:
            reasons.append(f"缺少 Baseline 场景：{scenario_id}")
            changes.append(
                EvalComparisonItem(
                    item_id=scenario_id,
                    change="removed",
                    blocking=True,
                    baseline_value=before.passed,
                    reason="blocking 场景被删除",
                )
            )
        elif before is not None and after is not None:
            change = "unchanged"
            if not before.passed and after.passed:
                change = "fixed"
            elif before.passed and not after.passed:
                change = "regression"
            changes.append(
                EvalComparisonItem(
                    item_id=scenario_id,
                    change=change,
                    blocking=change == "regression",
                    baseline_value=before.passed,
                    candidate_value=after.passed,
                )
            )

    safety_regression = bool(candidate.safety_violations)
    incompatible = bool(reasons)
    has_regression = any(item.blocking for item in changes) or safety_regression
    status = (
        "incomparable"
        if incompatible
        else "failed"
        if candidate.status != "passed" or has_regression
        else "passed"
    )
    metric_changes = (
        EvalComparisonItem(
            item_id="safety_violation_count",
            change="regression" if safety_regression else "unchanged",
            blocking=safety_regression,
            baseline_value=len(baseline.safety_violations),
            candidate_value=len(candidate.safety_violations),
        ),
    )
    return EvalComparison(
        baseline_id=baseline.baseline_id,
        status=status,
        compatible=not incompatible,
        reasons=tuple(reasons),
        scenario_changes=tuple(changes),
        metric_changes=metric_changes,
    )


def accept_baseline(
    report: EvalRunReport,
    output: Path,
    *,
    reason: str,
    replace: bool = False,
    accepted_at: datetime | None = None,
) -> EvalBaseline:
    """显式接受通过且安全的 Run；默认拒绝覆盖已有 Baseline。"""

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("接受 Baseline 必须提供非空原因")
    if report.status != "passed" or not all(suite.passed for suite in report.suites):
        raise ValueError("只有完整通过的 Run 才能成为 Baseline")
    if report.safety_violations:
        raise ValueError("存在安全违规的 Run 不能成为 Baseline")
    required_fingerprints = (
        report.manifest.source_fingerprint,
        report.manifest.catalog_fingerprint,
        report.manifest.fixture_fingerprint,
        report.manifest.threshold_fingerprint,
        report.result_fingerprint,
    )
    if not all(required_fingerprints):
        raise ValueError("Run 缺少 Baseline 所需指纹")
    previous: EvalBaseline | None = None
    if output.exists():
        if not replace:
            raise FileExistsError("Baseline 已存在，替换时必须显式使用 replace")
        previous = EvalBaseline.model_validate_json(output.read_text("utf-8"))
    timestamp = accepted_at or datetime.now(UTC)
    identity = {
        "channel": report.manifest.channel,
        "profile_id": report.manifest.profile_id,
        "accepted_at": timestamp.isoformat(),
        "result_fingerprint": report.result_fingerprint,
        "supersedes": previous.baseline_id if previous else None,
    }
    baseline = EvalBaseline(
        schema_version=report.manifest.schema_version,
        baseline_id=f"baseline-{content_hash(identity)[:16]}",
        channel=report.manifest.channel,
        profile_id=report.manifest.profile_id,
        accepted_at=timestamp,
        acceptance_reason=normalized_reason,
        supersedes_baseline_id=previous.baseline_id if previous else None,
        manifest=report.manifest,
        suites=report.suites,
        aggregate_metrics=report.aggregate_metrics,
        result_fingerprint=report.result_fingerprint,
        safety_violations=report.safety_violations,
    )
    payload = baseline.model_dump(mode="json")
    assert_no_sensitive_artifact(payload)
    _atomic_write(
        output,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode(
            "utf-8"
        ),
    )
    return baseline


def read_baseline(path: Path) -> EvalBaseline:
    """读取、校验并再次执行脱敏检查。"""

    baseline = EvalBaseline.model_validate_json(path.read_text("utf-8"))
    assert_no_sensitive_artifact(baseline.model_dump(mode="json"))
    return baseline


def status_exit_code(status: str) -> int:
    """把统一 Run 状态转换为稳定 CLI 退出码。"""

    return {"passed": 0, "failed": 1, "incomparable": 2, "incomplete": 3}.get(
        status,
        4,
    )
