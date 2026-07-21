"""定义 v0.8 统一 Eval、Baseline、比较与发布门禁契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvalChannel = Literal["offline", "provider"]
EvalRunStatus = Literal["passed", "failed", "incomparable", "incomplete"]
EvalFailureLayer = Literal[
    "fixture",
    "harness",
    "environment",
    "context",
    "model",
    "tool",
    "policy",
    "storage",
    "verification",
    "comparison",
    "safety",
]
EvalMetricKind = Literal["result", "trajectory", "efficiency", "safety"]
EvalMetricDirection = Literal["exact", "minimum", "maximum", "zero"]
EvalCheckStatus = Literal["passed", "failed", "skipped", "incomplete"]
MetricScalar = str | int | float | bool | None


class EvalMetricDefinition(BaseModel):
    """声明一个可比较指标的单位、方向和阻断语义。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    kind: EvalMetricKind
    unit: str
    direction: EvalMetricDirection
    threshold: int | float | bool | None = None
    blocking: bool = True


class EvalScenarioDescriptor(BaseModel):
    """描述一条固定场景的版本、标签、约束和稳定摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    legacy_scenario_id: str
    suite_id: str
    suite_version: str
    category: str
    capability_tags: tuple[str, ...]
    risk_tags: tuple[str, ...]
    expected_terminal: str | None = None
    metric_ids: tuple[str, ...] = ()
    safety_invariant_ids: tuple[str, ...] = ()
    fixture_refs: tuple[str, ...] = ()
    descriptor_hash: str


class EvalSuiteDescriptor(BaseModel):
    """保存一个版本化 Suite 及其全部固定场景目录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: str
    suite_version: str
    channel: EvalChannel = "offline"
    capability_tags: tuple[str, ...]
    metric_definitions: tuple[EvalMetricDefinition, ...]
    safety_invariant_ids: tuple[str, ...]
    scenarios: tuple[EvalScenarioDescriptor, ...]
    descriptor_hash: str


class EvalCatalog(BaseModel):
    """保存显式 Registry 生成的全部 Suite 和内容指纹。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    catalog_version: str
    suites: tuple[EvalSuiteDescriptor, ...]
    fingerprint: str


class EvalScenarioOutcome(BaseModel):
    """保存一条场景的规范化结果、指标和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    legacy_scenario_id: str
    suite_id: str
    category: str
    passed: bool
    expected_terminal: str | None = None
    actual_terminal: str | None = None
    metrics: dict[str, MetricScalar] = Field(default_factory=dict)
    safety_violations: tuple[str, ...] = ()
    failure_layer: EvalFailureLayer | None = None
    failure_code: str | None = None
    trace_refs: tuple[str, ...] = ()


class EvalSuiteOutcome(BaseModel):
    """汇总单个 Suite 的场景、指标、安全结果和原结论。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: str
    suite_version: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    metrics: dict[str, MetricScalar]
    safety_violations: tuple[str, ...]
    category_counts: dict[str, int]
    scenarios: tuple[EvalScenarioOutcome, ...]
    original_suite_name: str


class EvalRunManifest(BaseModel):
    """记录一次 Eval Run 的代码、数据、模型和门槛版本。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    run_id: str
    channel: EvalChannel
    profile_id: str
    profile_version: str
    started_at: datetime
    completed_at: datetime | None = None
    git_commit: str | None = None
    git_dirty: bool
    source_fingerprint: str
    catalog_version: str
    catalog_fingerprint: str
    fixture_fingerprint: str
    application_version: str
    python_version: str
    node_version: str | None = None
    dependency_fingerprints: dict[str, str]
    model_provider: str
    model_name: str
    prompt_version: str
    schema_contract_version: str
    toolset_version: str
    policy_version: str
    context_version: str
    migration_head: str
    threshold_fingerprint: str
    baseline_id: str | None = None


class EvalComparisonItem(BaseModel):
    """描述 Candidate 中一个场景或指标相对 Baseline 的变化。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    change: Literal[
        "unchanged",
        "fixed",
        "regression",
        "new",
        "removed",
        "improved",
        "degraded",
    ]
    blocking: bool
    baseline_value: MetricScalar = None
    candidate_value: MetricScalar = None
    reason: str | None = None


class EvalComparison(BaseModel):
    """保存 Candidate 与 Baseline 的兼容性和逐项差异。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_id: str
    status: EvalRunStatus
    compatible: bool
    reasons: tuple[str, ...] = ()
    scenario_changes: tuple[EvalComparisonItem, ...] = ()
    metric_changes: tuple[EvalComparisonItem, ...] = ()


class EvalReleaseCheckResult(BaseModel):
    """保存一个受控工程检查的退出状态和脱敏摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str
    status: EvalCheckStatus
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    output_hash: str | None = None
    summary: str | None = None


class EvalRunReport(BaseModel):
    """保存一次统一 Eval Run 的事实、比较和发布结论。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: EvalRunManifest
    status: EvalRunStatus
    suites: tuple[EvalSuiteOutcome, ...]
    aggregate_metrics: dict[str, MetricScalar]
    safety_violations: tuple[str, ...]
    release_checks: tuple[EvalReleaseCheckResult, ...] = ()
    result_fingerprint: str
    comparison: EvalComparison | None = None


class EvalBaseline(BaseModel):
    """保存经显式接受的脱敏 Run 结果和前一 Baseline 引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    baseline_id: str
    channel: EvalChannel
    profile_id: str
    accepted_at: datetime
    acceptance_reason: str
    supersedes_baseline_id: str | None = None
    manifest: EvalRunManifest
    suites: tuple[EvalSuiteOutcome, ...]
    aggregate_metrics: dict[str, MetricScalar]
    result_fingerprint: str
    safety_violations: tuple[str, ...]
