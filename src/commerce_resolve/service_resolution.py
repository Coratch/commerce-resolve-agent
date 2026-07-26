"""定义 v1.3 组合售后咨询的公开结构化方案。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from commerce_resolve.models import PolicyCitation

ServiceAllowedAction = Literal[
    "view_order",
    "view_policy",
    "request_refund",
    "upgrade_l2",
    "provide_information",
]
ServiceStopReason = Literal[
    "completed",
    "needs_user_input",
    "insufficient_evidence",
    "conflicting_evidence",
    "order_unavailable",
    "tool_failed",
    "model_unavailable",
]


class ServiceVerifiedFact(BaseModel):
    """表示一条来自订单、物流或政策证据的已验证事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal["order", "shipment", "policy"]
    statement: str = Field(min_length=1, max_length=500)
    evidence_id: str = Field(min_length=1, max_length=200)


class ServiceProgressStep(BaseModel):
    """表示组合咨询中可公开的一步处理结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9_]{2,50}$")
    title: str = Field(min_length=1, max_length=100)
    state: Literal["completed", "blocked", "skipped"]


class ServiceResolution(BaseModel):
    """保存可持久化、可恢复且不含隐藏推理的客户服务方案。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    goal: str = Field(min_length=1, max_length=500)
    verified_facts: tuple[ServiceVerifiedFact, ...] = ()
    missing_information: tuple[str, ...] = ()
    policy_evidence: tuple[PolicyCitation, ...] = ()
    recommendations: tuple[str, ...] = ()
    allowed_actions: tuple[ServiceAllowedAction, ...] = ()
    progress: tuple[ServiceProgressStep, ...] = ()
    stop_reason: ServiceStopReason
    next_step: str = Field(min_length=1, max_length=500)
