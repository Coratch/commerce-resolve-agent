"""实现不依赖 LLM 的退款资格、业务指纹和预览构造规则。"""

import hashlib
import json

from commerce_resolve.business_models import format_minor_units
from commerce_resolve.models import (
    PolicyFact,
    RefundContext,
    RefundEligibility,
    RefundPreview,
    RefundReason,
)

REQUIRED_REFUND_FACT_IDS = (
    "refund.eligibility.pre_fulfillment",
    "refund.method.original",
    "refund.process.application",
)


def _digest(payload: object) -> str:
    """对规范化 JSON 计算稳定 SHA-256 摘要。"""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_facts_fingerprint(
    context: RefundContext,
    *,
    policy_version: str,
    policy_fact_ids: tuple[str, ...],
) -> str:
    """绑定最新业务事实、政策版本和事实标识，供批准前检测过期。"""

    return _digest(
        {
            "context": context.model_dump(mode="json"),
            "policy_version": policy_version,
            "policy_fact_ids": sorted(policy_fact_ids),
        }
    )


def assess_refund(
    context: RefundContext,
    facts: tuple[PolicyFact, ...],
) -> RefundEligibility:
    """按固定状态矩阵和已验证政策事实计算整单可退款余额。"""

    facts_by_id = {fact.fact_id: fact for fact in facts}
    selected = tuple(
        facts_by_id[fact_id]
        for fact_id in REQUIRED_REFUND_FACT_IDS
        if fact_id in facts_by_id
    )
    citations = tuple(fact.citation for fact in selected)
    if len(selected) != len(REQUIRED_REFUND_FACT_IDS):
        return RefundEligibility(
            eligible=False,
            reason_code="refund_policy_evidence_missing",
            refundable_amount_minor=0,
            policy_fact_ids=tuple(fact.fact_id for fact in selected),
            citations=citations,
        )
    common = {
        "policy_fact_ids": tuple(fact.fact_id for fact in selected),
        "citations": citations,
    }
    if context.payment_id is None or context.payment_status is None:
        return RefundEligibility(
            eligible=False,
            reason_code="refund_payment_missing",
            refundable_amount_minor=0,
            **common,
        )
    if context.has_conflicting_refund:
        return RefundEligibility(
            eligible=False,
            reason_code="refund_conflict",
            refundable_amount_minor=0,
            currency=context.currency,
            channel=context.channel,
            **common,
        )
    refundable = max(
        context.paid_amount_minor - context.active_or_completed_refund_amount_minor,
        0,
    )
    if context.payment_status == "refunded" or refundable == 0:
        return RefundEligibility(
            eligible=False,
            reason_code="refund_balance_zero",
            refundable_amount_minor=0,
            currency=context.currency,
            channel=context.channel,
            **common,
        )
    if context.payment_status != "settled":
        return RefundEligibility(
            eligible=False,
            reason_code="refund_payment_not_settled",
            refundable_amount_minor=0,
            currency=context.currency,
            channel=context.channel,
            **common,
        )
    contradictory = (
        context.order_status in {"processing", "cancelled"}
        and context.shipment_status in {"in_transit", "delivered"}
    ) or (
        context.order_status == "delivered"
        and context.shipment_status not in {None, "delivered"}
    )
    if contradictory:
        return RefundEligibility(
            eligible=False,
            reason_code="refund_business_facts_conflict",
            refundable_amount_minor=0,
            currency=context.currency,
            channel=context.channel,
            **common,
        )
    if context.order_status not in {"processing", "cancelled"} or (
        context.shipment_status not in {None, "preparing"}
    ):
        return RefundEligibility(
            eligible=False,
            reason_code="refund_requires_return_flow",
            refundable_amount_minor=0,
            currency=context.currency,
            channel=context.channel,
            **common,
        )
    if context.currency is None or context.channel is None:
        return RefundEligibility(
            eligible=False,
            reason_code="refund_payment_invalid",
            refundable_amount_minor=0,
            **common,
        )
    return RefundEligibility(
        eligible=True,
        reason_code="refund_eligible",
        refundable_amount_minor=refundable,
        currency=context.currency,
        channel=context.channel,
        **common,
    )


def build_refund_preview(
    *,
    action_id: str,
    task_id: str,
    reason: RefundReason,
    context: RefundContext,
    eligibility: RefundEligibility,
    policy_version: str,
) -> RefundPreview:
    """从资格结果构造服务端唯一退款预览并绑定内容哈希。"""

    if (
        not eligibility.eligible
        or eligibility.refundable_amount_minor <= 0
        or eligibility.currency is None
        or eligibility.channel is None
        or context.payment_status != "settled"
    ):
        raise ValueError("cannot build preview for ineligible refund")
    facts_fingerprint = build_facts_fingerprint(
        context,
        policy_version=policy_version,
        policy_fact_ids=eligibility.policy_fact_ids,
    )
    payload = {
        "action_id": action_id,
        "task_id": task_id,
        "order_id": context.order_id,
        "reason": reason.model_dump(mode="json"),
        "amount_minor": eligibility.refundable_amount_minor,
        "currency": eligibility.currency,
        "channel": eligibility.channel,
        "order_status": context.order_status,
        "shipment_status": context.shipment_status,
        "payment_status": context.payment_status,
        "policy_fact_ids": eligibility.policy_fact_ids,
        "policy_version": policy_version,
        "facts_fingerprint": facts_fingerprint,
    }
    return RefundPreview(
        action_id=action_id,
        task_id=task_id,
        order_id=context.order_id,
        reason=reason,
        amount_minor=eligibility.refundable_amount_minor,
        display_amount=format_minor_units(eligibility.refundable_amount_minor),
        currency=eligibility.currency,
        channel=eligibility.channel,
        order_status=context.order_status,
        shipment_status=context.shipment_status,
        payment_status="settled",
        policy_fact_ids=eligibility.policy_fact_ids,
        citations=eligibility.citations,
        policy_version=policy_version,
        facts_fingerprint=facts_fingerprint,
        preview_hash=_digest(payload),
    )
