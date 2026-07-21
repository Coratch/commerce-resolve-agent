"""确定性构建 L2 Context Pack 与不含正文的 Context Manifest。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime

from commerce_resolve.l2_models import (
    CustomerPreference,
    L2ContextDisposition,
    L2ContextFreshness,
    L2ContextManifest,
    L2ContextManifestItem,
    L2ContextPack,
    L2ContextPublicMessage,
    L2ContextPublicSummary,
    L2ContextSourceType,
    L2FailureAttribution,
    L2Observation,
    L2RuntimeState,
)

CONTEXT_POLICY_VERSION = "v0.7.0"
MAX_MESSAGE_CANDIDATES = 100
MAX_SELECTED_MESSAGES = 12
MAX_SELECTED_OBSERVATIONS = 12
MAX_SELECTED_PREFERENCES = 3
MAX_CONTEXT_INPUT_ESTIMATED_TOKENS = 4_000
MODEL_OUTPUT_TOKEN_RESERVE = 800

_ORDER_PATTERN = re.compile(r"\bORD-[A-Z0-9-]{3,32}\b", re.IGNORECASE)
_ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]+")
_TOPIC_TERMS = {
    "订单",
    "物流",
    "退款",
    "退货",
    "换货",
    "政策",
    "支付",
    "签收",
    "运输",
}


@dataclass(frozen=True)
class _Candidate:
    """保存 Context Builder 调用内的候选正文和确定性排序字段。"""

    candidate_id: str
    source_type: L2ContextSourceType
    source_ref: str
    source_version: str | None
    content: str
    payload: object
    essential: bool
    priority: int
    freshness: L2ContextFreshness
    relevance_rank: int
    sequence_no: int


@dataclass(frozen=True)
class L2ContextBuildResult:
    """返回可选模型 Pack、Manifest 和调用前的确定性失败归因。"""

    pack: L2ContextPack | None
    manifest: L2ContextManifest
    failure_attribution: L2FailureAttribution | None = None

    @property
    def ready(self) -> bool:
        """表示 Context 已满足调用模型的全部前置条件。"""

        return self.pack is not None and self.failure_attribution is None


def estimate_input_tokens(serialized_context: str) -> int:
    """用固定保守字符比估算输入 Token，确保离线结果可复现。"""

    return max(1, (len(serialized_context) + 1) // 2)


def estimate_total_tokens(
    serialized_context: str,
    *,
    output_reserve: int = MODEL_OUTPUT_TOKEN_RESERVE,
) -> int:
    """返回输入估算与非负输出预留之和，供 Case 总预算预占。"""

    return estimate_input_tokens(serialized_context) + max(0, output_reserve)


def _hash_text(value: str) -> str:
    """返回规范化数据的稳定 SHA-256 十六进制摘要。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_fingerprint(value: object) -> str:
    """对规范化公开领域值生成来源版本，不作为授权或业务幂等键。"""

    return _hash_text(_canonical_json(value))


def refund_source_fingerprint(refunds: tuple[object, ...]) -> str:
    """按退款标识稳定排序后计算当前订单退款集合版本。"""

    normalized = sorted(
        [
            item.model_dump(mode="json", exclude_none=True)
            if hasattr(item, "model_dump")
            else item
            for item in refunds
        ],
        key=lambda item: _canonical_json(item),
    )
    return source_fingerprint(normalized)


def _stable_candidate_id(source_type: str, source_ref: str, version: str) -> str:
    """根据来源、引用和版本生成 Manifest 内稳定候选标识。"""

    return f"ctx-{_hash_text(f'{source_type}:{source_ref}:{version}')[:40]}"


def _normalize_text(value: str) -> str:
    """规范化公开文本，供去重和相关性判断，不改变原始展示内容。"""

    return " ".join(value.strip().lower().split())


def _text_tokens(value: str) -> frozenset[str]:
    """提取订单号、ASCII token、中文 bigram 和有限售后主题词。"""

    normalized = _normalize_text(value)
    tokens = {item.upper() for item in _ORDER_PATTERN.findall(value)}
    tokens.update(_ASCII_TOKEN_PATTERN.findall(normalized))
    for segment in _CJK_PATTERN.findall(normalized):
        if len(segment) == 1:
            tokens.add(segment)
            continue
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
    tokens.update(term for term in _TOPIC_TERMS if term in normalized)
    return frozenset(tokens)


def _source_version(observation: L2Observation) -> str | None:
    """从 Observation 的 discriminated source metadata 读取版本。"""

    metadata = observation.source_metadata
    if metadata is None:
        return None
    if metadata.kind == "policy":
        return f"{metadata.corpus_version}:{metadata.corpus_hash}"
    return metadata.source_version


def _observation_order_id(observation: L2Observation) -> str | None:
    """从业务 Observation metadata 提取服务端验证过的订单号。"""

    metadata = observation.source_metadata
    if metadata is None or metadata.kind not in {"order", "shipment", "refund"}:
        return None
    return metadata.order_id


def _canonical_json(value: object) -> str:
    """使用稳定 JSON 格式序列化 Pydantic 或普通结构。"""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)  # type: ignore[union-attr]
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _context_input_budget(runtime: L2RuntimeState) -> int:
    """根据 Case 剩余总 Token 预算计算本次最大输入预算。"""

    remaining = (
        runtime.budget_limits.max_estimated_tokens
        - runtime.budget.estimated_tokens_used
        - MODEL_OUTPUT_TOKEN_RESERVE
    )
    return max(0, min(MAX_CONTEXT_INPUT_ESTIMATED_TOKENS, remaining))


def _control_candidate(runtime: L2RuntimeState) -> _Candidate:
    """构造不可由用户数据覆盖的工具和剩余预算候选。"""

    content = _canonical_json(
        {
            "access_mode": "registered",
            "allowed_tools": runtime.allowed_tools,
            "remaining_steps": max(
                0,
                runtime.budget_limits.max_steps - runtime.budget.steps_used,
            ),
            "remaining_model_calls": max(
                0,
                runtime.budget_limits.max_model_calls - runtime.budget.model_calls_used,
            ),
            "remaining_tool_calls": max(
                0,
                runtime.budget_limits.max_tool_calls - runtime.budget.tool_calls_used,
            ),
        }
    )
    return _Candidate(
        candidate_id=_stable_candidate_id("control", "harness", CONTEXT_POLICY_VERSION),
        source_type="control",
        source_ref="harness",
        source_version=CONTEXT_POLICY_VERSION,
        content=content,
        payload=content,
        essential=True,
        priority=1,
        freshness="not_applicable",
        relevance_rank=100,
        sequence_no=0,
    )


def _goal_candidates(runtime: L2RuntimeState) -> list[_Candidate]:
    """构造 Case 目标、关联实体和最新补充等不可裁剪候选。"""

    values = [("issue", runtime.issue_summary)]
    if runtime.related_order_id is not None:
        values.append(("order", runtime.related_order_id))
    if runtime.latest_user_input:
        values.append(("latest-user-input", runtime.latest_user_input))
    candidates = []
    for index, (name, content) in enumerate(values):
        version = _hash_text(content)
        candidates.append(
            _Candidate(
                candidate_id=_stable_candidate_id("case_goal", name, version),
                source_type="case_goal",
                source_ref=name,
                source_version=version,
                content=content,
                payload=content,
                essential=True,
                priority=2,
                freshness="not_applicable",
                relevance_rank=100,
                sequence_no=index,
            )
        )
    return candidates


def _message_candidates(
    messages: tuple[L2ContextPublicMessage, ...],
    *,
    anchor_tokens: frozenset[str],
    related_order_id: str | None,
) -> tuple[list[_Candidate], dict[str, tuple[L2ContextDisposition, str]]]:
    """构造消息候选并确定性标记无关项与重复项。"""

    candidates: list[_Candidate] = []
    statuses: dict[str, tuple[L2ContextDisposition, str]] = {}
    seen_content: set[str] = set()
    for message in sorted(
        messages[-MAX_MESSAGE_CANDIDATES:],
        key=lambda item: item.sequence_no,
        reverse=True,
    ):
        normalized = _normalize_text(message.content)
        version = _hash_text(f"{message.role}:{normalized}")
        candidate = _Candidate(
            candidate_id=_stable_candidate_id(
                "public_message", message.message_id, version
            ),
            source_type="public_message",
            source_ref=message.message_id,
            source_version=version,
            content=message.content,
            payload=message,
            essential=False,
            priority=6,
            freshness="not_applicable",
            relevance_rank=0,
            sequence_no=message.sequence_no,
        )
        candidates.append(candidate)
        dedup_key = f"{message.role}:{normalized}"
        if dedup_key in seen_content:
            statuses[candidate.candidate_id] = ("duplicate", "duplicate_message")
            continue
        seen_content.add(dedup_key)
        tokens = _text_tokens(message.content)
        exact_order = related_order_id is not None and related_order_id in tokens
        overlap = len(tokens.intersection(anchor_tokens))
        if not exact_order and overlap == 0:
            statuses[candidate.candidate_id] = ("irrelevant", "message_not_relevant")
            continue
        rank = 80 if exact_order else min(70, 40 + overlap)
        candidates[-1] = replace(candidate, relevance_rank=rank)
    return candidates, statuses


def _observation_candidates(
    observations: tuple[L2Observation, ...],
    *,
    related_order_id: str | None,
) -> tuple[list[_Candidate], dict[str, tuple[L2ContextDisposition, str]], bool]:
    """构造 Observation 候选，标记旧版本、未知来源、无关实体和政策冲突。"""

    candidates: list[_Candidate] = []
    statuses: dict[str, tuple[L2ContextDisposition, str]] = {}
    latest_by_source: dict[tuple[str, str], _Candidate] = {}
    policy_values: dict[str, set[str]] = {}
    conflict = False
    for observation in sorted(
        observations,
        key=lambda item: item.observed_at,
        reverse=True,
    ):
        version = _source_version(observation)
        is_fact_result = observation.result_code in {"found", "insufficient_evidence"}
        freshness: L2ContextFreshness = (
            "fresh"
            if version is not None
            else "unknown"
            if is_fact_result
            else "not_applicable"
        )
        source_type: L2ContextSourceType = (
            "policy_observation"
            if observation.source_type == "search_policy"
            else "business_observation"
        )
        candidate = _Candidate(
            candidate_id=_stable_candidate_id(
                source_type,
                observation.observation_id,
                version or "unknown",
            ),
            source_type=source_type,
            source_ref=f"{observation.source_type}:{observation.source_ref}",
            source_version=version,
            content=observation.summary,
            payload=observation,
            essential=is_fact_result,
            priority=4 if source_type == "policy_observation" else 3,
            freshness=freshness,
            relevance_rank=90,
            sequence_no=int(observation.observed_at.timestamp()),
        )
        candidates.append(candidate)
        if observation.source_type == "list_confirmed_preferences":
            statuses[candidate.candidate_id] = (
                "duplicate",
                "fresh_preference_source_selected",
            )
            continue
        order_id = _observation_order_id(observation)
        if related_order_id is not None and order_id not in {None, related_order_id}:
            statuses[candidate.candidate_id] = (
                "irrelevant",
                "observation_other_entity",
            )
            continue
        source_key = (observation.source_type, observation.source_ref)
        latest = latest_by_source.get(source_key)
        if latest is not None:
            disposition: L2ContextDisposition = (
                "duplicate"
                if latest.source_version == candidate.source_version
                else "stale"
            )
            statuses[candidate.candidate_id] = (
                disposition,
                "observation_superseded",
            )
            continue
        latest_by_source[source_key] = candidate
        if freshness == "unknown":
            statuses[candidate.candidate_id] = (
                "stale",
                "observation_version_unknown",
            )
            continue
        metadata = observation.source_metadata
        if metadata is not None and metadata.kind == "policy":
            for fact in metadata.facts:
                policy_values.setdefault(fact.rule_key, set()).add(
                    fact.normalized_value
                )
    conflicting_rules = {
        rule_key for rule_key, values in policy_values.items() if len(values) > 1
    }
    if conflicting_rules:
        conflict = True
        for candidate in candidates:
            observation = candidate.payload
            if not isinstance(observation, L2Observation):
                continue
            metadata = observation.source_metadata
            if metadata is None or metadata.kind != "policy":
                continue
            if any(fact.rule_key in conflicting_rules for fact in metadata.facts):
                statuses[candidate.candidate_id] = (
                    "conflict",
                    "current_policy_conflict",
                )
    return candidates, statuses, conflict


def _preference_candidates(
    preferences: tuple[CustomerPreference, ...],
) -> tuple[list[_Candidate], dict[str, tuple[L2ContextDisposition, str]]]:
    """按偏好类型选择当前最后确认版本，并标记重复项。"""

    candidates: list[_Candidate] = []
    statuses: dict[str, tuple[L2ContextDisposition, str]] = {}
    seen_types: set[str] = set()
    for preference in sorted(
        preferences,
        key=lambda item: item.last_confirmed_at,
        reverse=True,
    ):
        version = _hash_text(
            f"{preference.schema_version}:{preference.value}:"
            f"{preference.last_confirmed_at.isoformat()}"
        )
        candidate = _Candidate(
            candidate_id=_stable_candidate_id(
                "confirmed_preference", preference.memory_id, version
            ),
            source_type="confirmed_preference",
            source_ref=preference.memory_id,
            source_version=version,
            content=f"{preference.memory_type}={preference.value}",
            payload=preference,
            essential=False,
            priority=7,
            freshness="fresh",
            relevance_rank=20,
            sequence_no=int(preference.last_confirmed_at.timestamp()),
        )
        candidates.append(candidate)
        if preference.memory_type in seen_types:
            statuses[candidate.candidate_id] = (
                "duplicate",
                "preference_type_superseded",
            )
            continue
        seen_types.add(preference.memory_type)
    return candidates, statuses


def _pack_from_selected(
    runtime: L2RuntimeState,
    candidates: list[_Candidate],
    selected_ids: set[str],
    *,
    change_notes: tuple[str, ...],
) -> L2ContextPack:
    """把已选候选按各自强类型载荷组装为最终 Context Pack。"""

    messages = tuple(
        candidate.payload
        for candidate in sorted(candidates, key=lambda item: item.sequence_no)
        if candidate.candidate_id in selected_ids
        and isinstance(candidate.payload, L2ContextPublicMessage)
    )
    observations = tuple(
        candidate.payload
        for candidate in candidates
        if candidate.candidate_id in selected_ids
        and isinstance(candidate.payload, L2Observation)
    )
    preferences = tuple(
        candidate.payload
        for candidate in candidates
        if candidate.candidate_id in selected_ids
        and isinstance(candidate.payload, CustomerPreference)
    )
    return L2ContextPack(
        issue_summary=runtime.issue_summary,
        latest_user_input=runtime.latest_user_input,
        related_order_id=runtime.related_order_id,
        public_messages=messages[:MAX_SELECTED_MESSAGES],
        observations=observations[:MAX_SELECTED_OBSERVATIONS],
        confirmed_preferences=preferences[:MAX_SELECTED_PREFERENCES],
        allowed_tools=runtime.allowed_tools,
        remaining_steps=max(
            0,
            runtime.budget_limits.max_steps - runtime.budget.steps_used,
        ),
        remaining_model_calls=max(
            0,
            runtime.budget_limits.max_model_calls - runtime.budget.model_calls_used,
        ),
        remaining_tool_calls=max(
            0,
            runtime.budget_limits.max_tool_calls - runtime.budget.tool_calls_used,
        ),
        remaining_estimated_tokens=max(
            0,
            runtime.budget_limits.max_estimated_tokens
            - runtime.budget.estimated_tokens_used,
        ),
        change_notes=change_notes,
    )


def _manifest_item(
    candidate: _Candidate,
    disposition: L2ContextDisposition,
    reason_code: str,
) -> L2ContextManifestItem:
    """将包含正文的候选投影为不含正文的 Manifest item。"""

    return L2ContextManifestItem(
        candidate_id=candidate.candidate_id,
        source_type=candidate.source_type,
        source_ref=candidate.source_ref,
        source_version=candidate.source_version,
        freshness=candidate.freshness,
        disposition=disposition,
        reason_code=reason_code,
        essential=candidate.essential,
        estimated_input_tokens=estimate_input_tokens(candidate.content),
    )


def build_l2_context(
    *,
    runtime: L2RuntimeState,
    case_id: str,
    step_id: str,
    user_id: str,
    workspace_id: str,
    messages: tuple[L2ContextPublicMessage, ...] = (),
    preferences: tuple[CustomerPreference, ...] = (),
    change_notes: tuple[str, ...] = (),
    refresh_count: int = 0,
    context_preparation_ms: int = 0,
    now: datetime,
) -> L2ContextBuildResult:
    """按作用域、相关性、时效和预算生成一次 Pack 与 Manifest。"""

    candidates = [_control_candidate(runtime), *_goal_candidates(runtime)]
    statuses: dict[str, tuple[L2ContextDisposition, str]] = {}
    anchor_text = " ".join(
        value
        for value in (
            runtime.issue_summary,
            runtime.latest_user_input,
            runtime.related_order_id,
        )
        if value
    )
    anchor_tokens = _text_tokens(anchor_text)
    message_candidates, message_statuses = _message_candidates(
        messages,
        anchor_tokens=anchor_tokens,
        related_order_id=runtime.related_order_id,
    )
    observation_candidates, observation_statuses, policy_conflict = (
        _observation_candidates(
            runtime.observations,
            related_order_id=runtime.related_order_id,
        )
    )
    preference_candidates, preference_statuses = _preference_candidates(preferences)
    candidates.extend(observation_candidates)
    candidates.extend(message_candidates)
    candidates.extend(preference_candidates)
    statuses.update(observation_statuses)
    statuses.update(message_statuses)
    statuses.update(preference_statuses)

    selectable = [
        candidate for candidate in candidates if candidate.candidate_id not in statuses
    ]
    observations = [
        candidate
        for candidate in selectable
        if candidate.source_type in {"business_observation", "policy_observation"}
    ]
    messages_selected = sorted(
        (
            candidate
            for candidate in selectable
            if candidate.source_type == "public_message"
        ),
        key=lambda item: (-item.relevance_rank, -item.sequence_no, item.candidate_id),
    )
    for candidate in messages_selected[MAX_SELECTED_MESSAGES:]:
        statuses[candidate.candidate_id] = ("truncated", "message_count_limit")
    observations_selected = sorted(
        observations,
        key=lambda item: (item.priority, -item.sequence_no, item.candidate_id),
    )
    for candidate in observations_selected[MAX_SELECTED_OBSERVATIONS:]:
        statuses[candidate.candidate_id] = (
            "truncated",
            "observation_count_limit",
        )
    preference_selected = sorted(
        (
            candidate
            for candidate in selectable
            if candidate.source_type == "confirmed_preference"
        ),
        key=lambda item: (-item.sequence_no, item.candidate_id),
    )
    for candidate in preference_selected[MAX_SELECTED_PREFERENCES:]:
        statuses[candidate.candidate_id] = (
            "truncated",
            "preference_count_limit",
        )

    selected_ids = {
        candidate.candidate_id
        for candidate in candidates
        if candidate.candidate_id not in statuses
    }
    input_budget = _context_input_budget(runtime)
    pack = _pack_from_selected(
        runtime,
        candidates,
        selected_ids,
        change_notes=change_notes,
    )
    pack_tokens = estimate_input_tokens(_canonical_json(pack))
    optional_drop_order = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.candidate_id in selected_ids and not candidate.essential
        ),
        key=lambda item: (
            -item.priority,
            item.relevance_rank,
            item.sequence_no,
            item.candidate_id,
        ),
    )
    while pack_tokens > input_budget and optional_drop_order:
        candidate = optional_drop_order.pop(0)
        selected_ids.remove(candidate.candidate_id)
        statuses[candidate.candidate_id] = ("truncated", "context_token_budget")
        pack = _pack_from_selected(
            runtime,
            candidates,
            selected_ids,
            change_notes=change_notes,
        )
        pack_tokens = estimate_input_tokens(_canonical_json(pack))

    failure: L2FailureAttribution | None = None
    if policy_conflict:
        failure = "context_conflict"
    selected_source_refs = {
        candidate.source_ref
        for candidate in candidates
        if candidate.candidate_id in selected_ids
    }
    stale_relevant = any(
        disposition == "stale"
        and candidate.essential
        and candidate.source_ref not in selected_source_refs
        for candidate in candidates
        for disposition, _ in [
            statuses.get(candidate.candidate_id, ("selected", "selected"))
        ]
    )
    if failure is None and stale_relevant:
        failure = "context_stale"
    if failure is None and (input_budget <= 0 or pack_tokens > input_budget):
        failure = "context_missing"

    essential_complete = failure not in {
        "context_missing",
        "context_stale",
        "context_conflict",
    }
    if failure is not None:
        pack = None
    for candidate in candidates:
        if candidate.candidate_id not in statuses:
            statuses[candidate.candidate_id] = ("selected", "selected_by_policy")
    items = tuple(
        _manifest_item(candidate, *statuses[candidate.candidate_id])
        for candidate in sorted(
            candidates,
            key=lambda item: (item.priority, item.sequence_no, item.candidate_id),
        )
    )
    counts = {
        disposition: sum(item.disposition == disposition for item in items)
        for disposition in (
            "selected",
            "duplicate",
            "irrelevant",
            "stale",
            "conflict",
            "out_of_scope",
            "truncated",
        )
    }
    candidate_tokens = sum(item.estimated_input_tokens for item in items)
    selected_tokens = sum(
        item.estimated_input_tokens for item in items if item.disposition == "selected"
    )
    reduction = (
        ((candidate_tokens - selected_tokens) * 10_000) // candidate_tokens
        if candidate_tokens
        else 0
    )
    selected_observations = pack.observations if pack is not None else ()
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for observation in selected_observations
            for evidence_id in observation.evidence_ids
        )
    )[:16]
    source_types = tuple(
        dict.fromkeys(
            item.source_type for item in items if item.disposition == "selected"
        )
    )
    public_summary = L2ContextPublicSummary(
        source_types=source_types,
        selected_count=counts["selected"],
        public_evidence_ids=evidence_ids,
        truncated=counts["truncated"] > 0,
        facts_refreshed=refresh_count,
        state_changed=bool(change_notes),
        essential_complete=essential_complete,
    )
    pack_payload = (
        _canonical_json(pack)
        if pack is not None
        else _canonical_json([item.model_dump(mode="json") for item in items])
    )
    manifest_key = f"{case_id}:{step_id}:{CONTEXT_POLICY_VERSION}"
    manifest_id = f"manifest-{_hash_text(manifest_key)[:40]}"
    manifest = L2ContextManifest(
        manifest_id=manifest_id,
        case_id=case_id,
        step_id=step_id,
        context_policy_version=CONTEXT_POLICY_VERSION,
        scope_fingerprint=_hash_text(
            f"commerce-resolve:{user_id}:{workspace_id}:{case_id}"
        ),
        pack_hash=_hash_text(pack_payload),
        essential_complete=essential_complete,
        candidate_count=len(items),
        selected_count=counts["selected"],
        duplicate_count=counts["duplicate"],
        irrelevant_count=counts["irrelevant"],
        stale_count=counts["stale"],
        conflict_count=counts["conflict"],
        out_of_scope_count=counts["out_of_scope"],
        truncated_count=counts["truncated"],
        refresh_count=refresh_count,
        candidate_estimated_tokens=candidate_tokens,
        selected_estimated_tokens=selected_tokens,
        pack_estimated_input_tokens=pack_tokens,
        input_budget_tokens=input_budget,
        reduction_basis_points=max(0, min(10_000, reduction)),
        truncated=counts["truncated"] > 0,
        failure_attribution=failure,
        public_summary=public_summary,
        items=items,
        context_preparation_ms=context_preparation_ms,
        created_at=now,
    )
    return L2ContextBuildResult(
        pack=pack,
        manifest=manifest,
        failure_attribution=failure,
    )
