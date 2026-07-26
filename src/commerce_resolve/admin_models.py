"""定义 v1.2 运营控制台使用的脱敏领域模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

AdminActionResult = Literal["succeeded", "failed"]
AdminEvalState = Literal["missing", "incompatible", "failed", "passed"]


class AdminCustomer(BaseModel):
    """表示后台准备 Mock 数据所需的有限客户身份。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    username: str
    status: Literal["active", "disabled"]
    role: Literal["customer", "admin"]
    workspace_id: str
    dataset_version: str | None = None
    dataset_status: Literal["initializing", "ready", "resetting", "failed"] | None
    reset_generation: int
    order_count: int
    initialized_at: datetime | None = None
    created_at: datetime


class AdminInvitation(BaseModel):
    """表示不含邀请码明文和摘要的邀请使用状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invitation_id: str
    expires_at: datetime
    max_uses: int
    used_count: int
    revoked: bool
    created_at: datetime


class AdminAuditRecord(BaseModel):
    """表示可向管理员公开的脱敏后台写操作记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str
    admin_user_id: str
    target_user_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    result: AdminActionResult
    parameter_summary: dict[str, Any]
    created_at: datetime


class AdminRunSummary(BaseModel):
    """表示不含客户正文的一次 Agent Run 运营摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    request_kind: str
    status: str
    pending_action: str | None = None
    public_error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None


class AdminRunEvent(BaseModel):
    """表示经过字段白名单投影的 Agent Run 事件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: int
    event_type: str
    phase: str | None = None
    pending_action: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    created_at: datetime


class AdminRunDiagnostics(BaseModel):
    """表示可用 L2 Case 的有限工具、预算和失败诊断。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    status: str
    steps_used: int
    model_calls_used: int
    tool_calls_used: int
    estimated_tokens_used: int
    active_milliseconds: int
    stop_reason: str | None = None
    failure_attribution: str | None = None
    tool_categories: tuple[str, ...] = ()


class AdminRunDetail(BaseModel):
    """组合 Run 摘要、脱敏事件和可选 L2 诊断。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: AdminRunSummary
    events: tuple[AdminRunEvent, ...]
    diagnostics: AdminRunDiagnostics | None = None


class AdminEvalSuite(BaseModel):
    """表示一个 Eval Suite 的通过数量和安全违规数量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: str
    suite_version: str
    passed_scenarios: int
    total_scenarios: int
    passed: bool
    safety_violation_count: int


class AdminEvalSnapshot(BaseModel):
    """表示 Baseline 与最近 Candidate 的只读兼容性摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: AdminEvalState
    baseline_id: str | None = None
    candidate_run_id: str | None = None
    candidate_status: str | None = None
    application_version: str | None = None
    profile_version: str | None = None
    completed_at: datetime | None = None
    safety_violation_count: int = 0
    compatibility_reasons: tuple[str, ...] = ()
    suites: tuple[AdminEvalSuite, ...] = ()


class AdminSystemSnapshot(BaseModel):
    """表示不含本地路径和配置值的有限运行状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    migration_head: str
    live: bool
    ready: bool
    ready_error_code: str | None = None
    capabilities: dict[str, str]
    storage: dict[str, Literal["available", "unavailable"]]


class AdminOverview(BaseModel):
    """组合后台首页的权威计数、最近 Run、Eval 与系统状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    counts: dict[str, int]
    recent_runs: tuple[AdminRunSummary, ...]
    evaluation: AdminEvalSnapshot
    system: AdminSystemSnapshot
