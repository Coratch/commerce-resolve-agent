"""使用确定性规则选择政策事实、检测冲突并生成可引用回答。"""

from __future__ import annotations

from collections import defaultdict

from commerce_resolve.gateways import (
    PolicyRepository,
    PolicyRepositoryUnavailableError,
)
from commerce_resolve.models import (
    PolicyAnswerItem,
    PolicyConflict,
    PolicyDimension,
    PolicyEvidenceRef,
    PolicyFact,
    PolicyQuery,
    PolicyScope,
)

DIMENSION_LABELS: dict[PolicyDimension, str] = {
    "product_category": "商品类别（普通商品、服饰、卫生用品或数字商品）",
    "opened": "商品是否已经拆封或激活",
}


def resolve_evidence_facts(
    repository: PolicyRepository,
    evidence_refs: tuple[PolicyEvidenceRef, ...],
) -> tuple[PolicyFact, ...]:
    """按检索引用和内容哈希解析事实，拒绝丢失或被替换的证据。"""

    resolved: list[PolicyFact] = []
    seen_fact_ids: set[str] = set()
    for evidence in evidence_refs:
        for fact_id in evidence.fact_ids:
            if fact_id in seen_fact_ids:
                continue
            fact = repository.resolve_fact(fact_id, evidence.content_hash)
            if fact is None:
                raise PolicyRepositoryUnavailableError("政策引用无法解析")
            resolved.append(fact)
            seen_fact_ids.add(fact_id)
    return tuple(resolved)


def _scope_matches_known_dimensions(scope: PolicyScope, query: PolicyQuery) -> bool:
    """判断事实范围是否与用户已经提供的有限政策维度相容。"""

    if (
        query.product_category is not None
        and scope.product_categories
        and query.product_category not in scope.product_categories
    ):
        return False
    if query.opened is not None and scope.opened is not None:
        return query.opened == scope.opened
    return True


def _fact_matches_request(fact: PolicyFact, query: PolicyQuery) -> bool:
    """判断事实是否覆盖请求主题、方面和已知适用范围。"""

    return (
        fact.topic == query.topic
        and bool(set(fact.aspects).intersection(query.aspects))
        and _scope_matches_known_dimensions(fact.scope, query)
    )


def find_missing_dimensions(
    query: PolicyQuery,
    facts: tuple[PolicyFact, ...],
) -> tuple[PolicyDimension, ...]:
    """根据候选事实声明计算决定政策分支仍缺少的条件。"""

    missing: set[PolicyDimension] = set()
    for fact in facts:
        if not _fact_matches_request(fact, query):
            continue
        for dimension in fact.required_dimensions:
            if getattr(query, dimension) is None:
                missing.add(dimension)
    return tuple(dimension for dimension in DIMENSION_LABELS if dimension in missing)


def select_applicable_facts(
    query: PolicyQuery,
    facts: tuple[PolicyFact, ...],
) -> tuple[PolicyFact, ...]:
    """选择覆盖当前主题、方面和已知适用范围的规范化政策事实。"""

    return tuple(fact for fact in facts if _fact_matches_request(fact, query))


def unsupported_aspects(
    query: PolicyQuery,
    facts: tuple[PolicyFact, ...],
) -> tuple[str, ...]:
    """返回没有任何已选事实支持的请求方面。"""

    supported = {
        aspect for fact in facts for aspect in fact.aspects if aspect in query.aspects
    }
    return tuple(aspect for aspect in query.aspects if aspect not in supported)


def _scopes_overlap(left: PolicyScope, right: PolicyScope) -> bool:
    """判断两个有限适用范围是否可能同时作用于同一商品。"""

    categories_overlap = (
        not left.product_categories
        or not right.product_categories
        or bool(set(left.product_categories).intersection(right.product_categories))
    )
    opened_overlap = (
        left.opened is None or right.opened is None or left.opened == right.opened
    )
    return categories_overlap and opened_overlap


def detect_policy_conflicts(
    facts: tuple[PolicyFact, ...],
) -> tuple[PolicyConflict, ...]:
    """检测相同规则键、重叠范围但规范化值不同的事实对。"""

    grouped: dict[str, list[PolicyFact]] = defaultdict(list)
    for fact in facts:
        grouped[fact.rule_key].append(fact)

    conflicts: list[PolicyConflict] = []
    for rule_key, candidates in grouped.items():
        for left_index, left in enumerate(candidates):
            for right in candidates[left_index + 1 :]:
                if left.normalized_value == right.normalized_value:
                    continue
                if not _scopes_overlap(left.scope, right.scope):
                    continue
                conflicts.append(
                    PolicyConflict(
                        rule_key=rule_key,
                        fact_ids=(left.fact_id, right.fact_id),
                        claim_texts=(left.claim_text, right.claim_text),
                        citations=(left.citation, right.citation),
                    )
                )
    return tuple(conflicts)


def build_answer_items(facts: tuple[PolicyFact, ...]) -> tuple[PolicyAnswerItem, ...]:
    """将已验证事实绑定为不能脱离来源的回答条目。"""

    return tuple(
        PolicyAnswerItem(
            fact_id=fact.fact_id,
            claim_text=fact.claim_text,
            citation=fact.citation,
        )
        for fact in facts
    )


def format_citation(item: PolicyAnswerItem) -> str:
    """把服务端引用格式化为不暴露本机绝对路径的公开位置。"""

    citation = item.citation
    return (
        f"《{citation.title}》v{citation.version}，{citation.heading}，"
        f"{citation.source_relative_path}:{citation.line_start}-{citation.line_end}"
    )


def format_policy_answer(
    items: tuple[PolicyAnswerItem, ...],
    *,
    specific_order_eligibility: bool,
) -> str:
    """逐项输出规范化结论与其引用，并声明具体订单资格边界。"""

    lines = [f"- {item.claim_text}\n  来源：{format_citation(item)}" for item in items]
    if specific_order_eligibility:
        lines.append(
            "说明：以上是通用政策；当前版本不会查询订单或判断具体订单是否满足售后条件。"
        )
    return "\n".join(lines)


def format_missing_dimensions(dimensions: tuple[PolicyDimension, ...]) -> str:
    """生成一次明确的政策条件补充请求。"""

    labels = "、".join(DIMENSION_LABELS[item] for item in dimensions)
    return f"当前政策会根据具体条件区分，请补充：{labels}。"


def format_policy_conflicts(conflicts: tuple[PolicyConflict, ...]) -> str:
    """展示无法自动消解的冲突点及双方可定位来源。"""

    lines = ["当前有效政策存在无法自动消解的冲突，暂不能给出单一结论："]
    for conflict in conflicts:
        lines.append(f"- 冲突规则：{conflict.rule_key}")
        for claim_text, citation in zip(
            conflict.claim_texts,
            conflict.citations,
            strict=True,
        ):
            source = (
                f"《{citation.title}》v{citation.version}，{citation.heading}，"
                f"{citation.source_relative_path}:"
                f"{citation.line_start}-{citation.line_end}"
            )
            lines.append(f"  - {claim_text}\n    来源：{source}")
    return "\n".join(lines)
