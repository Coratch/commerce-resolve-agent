"""工作流与业务 Gateway 共享的强类型领域结果。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Intent = Literal[
    "order_inquiry",
    "policy_inquiry",
    "service_guidance",
    "refund_request",
    "l2_support_request",
    "unsupported_write",
    "unknown",
]
ServiceConcern = Literal[
    "order_status",
    "shipment_status",
    "policy",
    "refund_eligibility",
]
ToolOutcome = Literal["found", "unavailable", "temporarily_failed"]
OrderStatus = Literal["processing", "shipped", "delivered", "cancelled"]
ShipmentStatus = Literal["preparing", "in_transit", "delivered"]
PolicyTopic = Literal["return", "refund", "exchange"]
PolicyAspect = Literal[
    "window",
    "conditions",
    "shipping_fee",
    "exception",
    "process",
    "timing",
    "method",
]
ProductCategory = Literal["general", "apparel", "hygiene", "digital"]
PolicyRegion = Literal["CN", "overseas"]
PolicyDimension = Literal["product_category", "opened"]
PolicyDocumentStatus = Literal["published"]


class PolicyScope(BaseModel):
    """描述政策事实适用的商品类别与拆封状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_categories: tuple[ProductCategory, ...] = ()
    opened: bool | None = None


class PolicyQuery(BaseModel):
    """保存经过校验的政策主题、问题方面和适用条件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: PolicyTopic
    aspects: tuple[PolicyAspect, ...] = Field(min_length=1, max_length=7)
    search_terms: tuple[str, ...] = Field(default=(), max_length=8)
    product_category: ProductCategory | None = None
    opened: bool | None = None
    region: PolicyRegion = "CN"
    specific_order_eligibility: bool = False

    @model_validator(mode="after")
    def validate_search_terms(self) -> Self:
        """限制检索词长度并拒绝空白词，避免模型输出直接扩大查询。"""

        for term in self.search_terms:
            if not term.strip() or len(term) > 40:
                raise ValueError("policy search terms must be 1-40 characters")
        return self


class InterpretationContext(BaseModel):
    """向意图解释器提供可公开的最小多轮查询上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    previous_policy_query: PolicyQuery | None = None
    pending_refund_request: bool = False
    pending_intent_clarification: bool = False


class RefundReason(BaseModel):
    """保存用户表达的退款原因，不携带任何资格或金额判断。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Literal[
        "no_longer_needed",
        "quality_issue",
        "delivery_issue",
        "other",
    ]
    detail: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_other_detail(self) -> Self:
        """要求 other 原因提供可审计的非空说明。"""

        if self.code == "other" and not self.detail.strip():
            raise ValueError("other refund reason requires detail")
        return self


class Interpretation(BaseModel):
    """从最新用户消息中提取的结构化含义。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: Intent
    order_id: str | None = None
    policy_query: PolicyQuery | None = None
    concerns: tuple[ServiceConcern, ...] = Field(default=(), max_length=4)
    goal_summary: str | None = Field(default=None, max_length=500)
    refund_reason: RefundReason | None = None
    l2_issue_summary: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_policy_query_matches_intent(self) -> Self:
        """确保意图专属字段一致，并限制组合咨询至少包含两个关注点。"""

        if self.intent in {"policy_inquiry", "service_guidance"} and (
            self.policy_query is None
            and (
                self.intent == "policy_inquiry"
                or "policy" in self.concerns
                or "refund_eligibility" in self.concerns
            )
        ):
            raise ValueError("policy inquiries require policy_query")
        if (
            self.intent not in {"policy_inquiry", "service_guidance"}
            and self.policy_query is not None
        ):
            raise ValueError("non-policy inquiries cannot include policy_query")
        if self.intent == "service_guidance":
            if len(set(self.concerns)) < 2:
                raise ValueError("service guidance requires at least two concerns")
            if not self.goal_summary or not self.goal_summary.strip():
                raise ValueError("service guidance requires goal_summary")
        elif self.concerns or self.goal_summary is not None:
            raise ValueError("non-guidance inquiries cannot include guidance fields")
        if self.intent != "refund_request" and self.refund_reason is not None:
            raise ValueError("non-refund inquiries cannot include refund_reason")
        if self.intent == "l2_support_request" and not (
            self.l2_issue_summary and self.l2_issue_summary.strip()
        ):
            raise ValueError("l2 support requests require l2_issue_summary")
        if self.intent != "l2_support_request" and self.l2_issue_summary is not None:
            raise ValueError("non-l2 requests cannot include l2_issue_summary")
        return self


class OrderView(BaseModel):
    """v0.1 回复所需的最小订单数据。"""

    model_config = ConfigDict(frozen=True)

    order_id: str
    user_id: str
    status: OrderStatus


class ShipmentView(BaseModel):
    """v0.1 回复所需的最小物流数据。"""

    model_config = ConfigDict(frozen=True)

    order_id: str
    status: ShipmentStatus
    last_event: str
    estimated_delivery_at: date | None = None


class RefundContext(BaseModel):
    """保存确定性退款规则所需的最新业务事实快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    order_status: OrderStatus
    shipment_status: ShipmentStatus | None = None
    shipment_last_event: str | None = None
    shipment_updated_at: datetime | None = None
    evaluated_at: datetime | None = None
    payment_id: str | None = None
    paid_amount_minor: int = Field(default=0, ge=0)
    currency: Literal["CNY"] | None = None
    channel: Literal["mock_card", "mock_wallet"] | None = None
    payment_status: Literal["pending", "settled", "failed", "refunded"] | None = None
    active_or_completed_refund_amount_minor: int = Field(default=0, ge=0)
    has_conflicting_refund: bool = False


class RefundEligibility(BaseModel):
    """表示纯 Refund Policy 对业务事实和政策证据的判定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    reason_code: str
    refundable_amount_minor: int = Field(ge=0)
    currency: Literal["CNY"] | None = None
    channel: Literal["mock_card", "mock_wallet"] | None = None
    policy_fact_ids: tuple[str, ...] = ()
    citations: tuple[PolicyCitation, ...] = ()


class RefundPreview(BaseModel):
    """保存服务端生成、可恢复且不可由客户端改写的 R2 退款预览。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    task_id: str
    order_id: str
    reason: RefundReason
    amount_minor: int = Field(gt=0)
    display_amount: str
    currency: Literal["CNY"]
    channel: Literal["mock_card", "mock_wallet"]
    order_status: OrderStatus
    shipment_status: ShipmentStatus | None = None
    payment_status: Literal["settled"]
    risk: Literal["R2"] = "R2"
    policy_fact_ids: tuple[str, ...]
    citations: tuple[PolicyCitation, ...]
    policy_version: str
    facts_fingerprint: str
    preview_hash: str


class RefundExecutionResult(BaseModel):
    """表示退款命令的有限结果，不把 Gateway 返回值直接当作验证成功。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal[
        "succeeded",
        "business_rejected",
        "failed_before_write",
        "result_unknown",
        "verification_mismatch",
    ]
    action_id: str
    refund_id: str | None = None
    result_code: str


class RefundVerification(BaseModel):
    """保存从业务数据库回读所得的退款最终验证结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verified: bool
    action_id: str
    refund_id: str | None = None
    amount_minor: int = Field(ge=0)
    status: Literal["succeeded", "failed", "unknown"] | None = None
    result_code: str


class PolicyFactDefinition(BaseModel):
    """定义 manifest 中可进入最终回答的规范化政策事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str = Field(min_length=1, max_length=120)
    claim_text: str = Field(min_length=1, max_length=1000)
    rule_key: str = Field(min_length=1, max_length=120)
    normalized_value: str = Field(min_length=1, max_length=200)
    scope: PolicyScope = Field(default_factory=PolicyScope)
    required_dimensions: tuple[PolicyDimension, ...] = ()


class PolicySectionDefinition(BaseModel):
    """定义一个可检索政策章节及其结构化事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str = Field(min_length=1, max_length=120)
    heading: str = Field(min_length=1, max_length=200)
    topic: PolicyTopic
    aspects: tuple[PolicyAspect, ...] = Field(min_length=1, max_length=7)
    aliases: tuple[str, ...] = Field(default=(), max_length=20)
    facts: tuple[PolicyFactDefinition, ...] = Field(min_length=1)


class PolicyDocumentDefinition(BaseModel):
    """定义一个带版本、生效区间和章节清单的政策文档。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=40)
    effective_from: date
    effective_to: date | None = None
    region: str = Field(min_length=1, max_length=40)
    status: PolicyDocumentStatus
    path: str = Field(min_length=1, max_length=300)
    sections: tuple[PolicySectionDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_effective_interval(self) -> Self:
        """拒绝结束日期早于生效日期的政策版本。"""

        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be before effective_from")
        return self


class PolicyManifest(BaseModel):
    """定义可版本化政策语料的根清单。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(ge=1)
    corpus_version: str = Field(min_length=1, max_length=80)
    documents: tuple[PolicyDocumentDefinition, ...] = Field(min_length=1)


class PolicyCitation(BaseModel):
    """保存能够解析回确定政策原文的服务端引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    title: str
    version: str
    effective_from: date
    effective_to: date | None = None
    section_id: str
    heading: str
    source_relative_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    content_hash: str


class PolicyEvidenceRef(BaseModel):
    """保存检索候选的稳定标识、定位信息与排序分数。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    version: str
    section_id: str
    fact_ids: tuple[str, ...]
    score: float
    token_coverage: float = Field(ge=0.0, le=1.0)
    content_hash: str


class PolicyFact(BaseModel):
    """表示从当前索引解析出的完整、可引用政策事实。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    claim_text: str
    rule_key: str
    normalized_value: str
    scope: PolicyScope
    required_dimensions: tuple[PolicyDimension, ...]
    topic: PolicyTopic
    aspects: tuple[PolicyAspect, ...]
    citation: PolicyCitation


class PolicySearchResult(BaseModel):
    """保存一次政策检索的索引版本和候选证据引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_version: str
    corpus_hash: str
    evidence_refs: tuple[PolicyEvidenceRef, ...]


class PolicyConflict(BaseModel):
    """描述同一适用范围中无法自动消解的政策事实冲突。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_key: str
    fact_ids: tuple[str, str]
    claim_texts: tuple[str, str]
    citations: tuple[PolicyCitation, PolicyCitation]


class PolicyAnswerItem(BaseModel):
    """绑定一个规范化政策结论与其可定位引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    claim_text: str
    citation: PolicyCitation


class PolicyIndexSummary(BaseModel):
    """返回一次成功索引构建的可复现统计信息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    corpus_version: str
    corpus_hash: str
    document_count: int = Field(ge=0)
    section_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)


class ToolResult[ResultValue](BaseModel):
    """只读业务 Gateway 使用的统一返回结果。"""

    model_config = ConfigDict(frozen=True)

    outcome: ToolOutcome
    value: ResultValue | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_value_matches_outcome(self) -> Self:
        """校验工具状态与返回值是否满足统一结果契约。"""

        if self.outcome == "found" and self.value is None:
            raise ValueError("found results require a value")
        if self.outcome != "found" and self.value is not None:
            raise ValueError("non-found results cannot expose a value")
        return self


RefundEligibility.model_rebuild()
RefundPreview.model_rebuild()
