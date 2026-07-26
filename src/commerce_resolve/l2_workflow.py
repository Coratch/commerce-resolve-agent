"""在唯一主图中注册可恢复、可审批且有预算上限的 AI 深度处理 Harness。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from time import monotonic
from typing import Literal, cast
from uuid import uuid4

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import Dependencies
from commerce_resolve.l2_context import (
    CONTEXT_POLICY_VERSION,
    MAX_MESSAGE_CANDIDATES,
    build_l2_context,
    estimate_total_tokens,
)
from commerce_resolve.l2_gateways import (
    L2_MODEL_UNAVAILABLE_MESSAGE,
    L2Dependencies,
    L2ModelOutputInvalidError,
    L2ModelUnavailableError,
)
from commerce_resolve.l2_memory import confirm_preference, list_preferences
from commerce_resolve.l2_models import (
    AnswerDecision,
    AskUserDecision,
    L2CaseCreate,
    L2CaseStatus,
    L2CaseTransition,
    L2ContextPublicMessage,
    L2ContextPublicSummary,
    L2FailureAttribution,
    L2ModelCallStart,
    L2ModelRequest,
    L2Observation,
    L2PublicTraceEvent,
    L2RuntimeState,
    L2StopReason,
    L2ToolName,
    L2UpgradePreview,
    MemoryProposal,
    ProposeMemoryDecision,
    ProposeRefundDecision,
    StopDecision,
    ToolCallDecision,
)
from commerce_resolve.l2_observability import public_failure_message
from commerce_resolve.l2_policy import (
    budget_after_model_call,
    budget_after_tool_call,
    check_model_budget,
    decide_l2_upgrade,
    default_budget,
    tool_action_signature,
    validate_tool_call,
)
from commerce_resolve.l2_tools import L2ToolContext, L2ToolRegistry
from commerce_resolve.state import AgentState, RunContext

L2_UPGRADE_MESSAGE = (
    "这个问题需要进一步核对。经你同意后，AI 售后助手会在限定范围内"
    "读取订单、物流、Mock 退款状态、售后政策和你已确认的偏好，"
    "并可能继续询问补充信息。它不是真人客服，也不会自动执行退款。"
)
L2_CANCELLED_MESSAGE = "已停止进一步核对，未创建处理 Case，也未调用处理模型。"
L2_TOOLSET_VERSION = "v0.5.0"


def _require_l2(dependencies: Dependencies) -> L2Dependencies:
    """返回已装配的 L2 依赖，缺失时拒绝进入二线流程。"""

    if dependencies.l2 is None:
        raise ValueError("L2 dependencies are not configured")
    return dependencies.l2


def _scope(runtime: Runtime[RunContext]) -> BusinessScope:
    """从不可由模型覆盖的 RunContext 构造业务工具作用域。"""

    return BusinessScope(
        user_id=runtime.context.user_id,
        workspace_id=runtime.context.workspace_id,
        access_mode=runtime.context.access_mode,
    )


def _stable_id(prefix: str, *parts: object) -> str:
    """根据持久状态字段生成节点重放时保持一致的短标识。"""

    raw = ":".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
    return f"{prefix}-{digest}"


def _preview_hash(preview: dict[str, object]) -> str:
    """对服务端升级预览生成稳定哈希，防止恢复时接受篡改参数。"""

    payload = json.dumps(
        preview,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _message_text(message: BaseMessage) -> str | None:
    """提取可发送给 L2 模型的有限公开文本消息。"""

    return message.content[:2000] if isinstance(message.content, str) else None


def _state_public_messages(state: AgentState) -> tuple[L2ContextPublicMessage, ...]:
    """在未装配公开消息 Reader 的测试环境中构造有界兼容候选。"""

    result: list[L2ContextPublicMessage] = []
    for sequence_no, message in enumerate(state.get("messages", []), start=1):
        text = _message_text(message)
        if text is None or message.type not in {"human", "ai"}:
            continue
        result.append(
            L2ContextPublicMessage(
                message_id=(
                    message.id or _stable_id("message", sequence_no, message.type, text)
                ),
                sequence_no=sequence_no,
                role="user" if message.type == "human" else "assistant",
                content=text,
            )
        )
    return tuple(result[-MAX_MESSAGE_CANDIDATES:])


def _refresh_observations(
    l2: L2Dependencies,
    current: L2RuntimeState,
    runtime: Runtime[RunContext],
    *,
    step_id: str,
) -> tuple[tuple[L2Observation, ...], tuple[str, ...], int]:
    """调用只读 Freshness Reader，替换变化事实并屏蔽不可验证旧事实。"""

    if l2.freshness_reader is None:
        return current.observations, (), 0
    refreshed: list[L2Observation] = []
    notes: list[str] = []
    reads = 0
    for observation in current.observations:
        metadata = observation.source_metadata
        if metadata is None or metadata.kind == "preference":
            refreshed.append(observation)
            continue
        result = l2.freshness_reader.refresh(
            observation,
            scope=_scope(runtime),
            as_of=runtime.context.as_of or date.today(),
            step_id=step_id,
            now=l2.clock(),
        )
        reads += 1
        if result.observation is not None:
            refreshed.append(result.observation)
            if result.changed:
                notes.append(f"{metadata.kind}_fact_refreshed")
            continue
        refreshed.append(observation.model_copy(update={"source_metadata": None}))
    return tuple(refreshed[-20:]), tuple(dict.fromkeys(notes)), reads


def _trace(
    l2: L2Dependencies,
    runtime: Runtime[RunContext],
    l2_state: L2RuntimeState,
    *,
    event_key: str,
    event_type: str,
    result_code: str,
    tool_category: str | None = None,
    parameter_summary: dict[str, str] | None = None,
    evidence_ids: tuple[str, ...] = (),
    duration_ms: int = 0,
    context_summary: L2ContextPublicSummary | None = None,
) -> None:
    """幂等保存一条不含 Prompt、隐藏推理和完整工具正文的公开事件。"""

    if l2_state.case_id is None:
        raise ValueError("recording L2 trace requires case_id")
    l2.case_repository.append_event_once(
        user_id=runtime.context.user_id,
        workspace_id=runtime.context.workspace_id,
        event=L2PublicTraceEvent(
            event_id=str(uuid4()),
            case_id=l2_state.case_id,
            event_key=event_key,
            step_number=l2_state.budget.steps_used,
            event_type=event_type,
            tool_category=cast(L2ToolName | None, tool_category),
            risk="R0" if tool_category is not None else None,
            parameter_summary=parameter_summary,
            result_code=result_code,
            evidence_ids=evidence_ids,
            duration_ms=duration_ms,
            context_summary=context_summary,
            created_at=l2.clock(),
        ),
    )


def _transition(
    l2: L2Dependencies,
    runtime: Runtime[RunContext],
    l2_state: L2RuntimeState,
    *,
    status: L2CaseStatus,
    stop_reason: L2StopReason | None = None,
    final_response: str | None = None,
) -> None:
    """同步 Case 公开状态和单调预算，不把 Repository 对象写入 State。"""

    if l2_state.case_id is None:
        raise ValueError("transitioning L2 case requires case_id")
    l2.case_repository.transition_case(
        case_id=l2_state.case_id,
        user_id=runtime.context.user_id,
        workspace_id=runtime.context.workspace_id,
        transition=L2CaseTransition(
            expected_statuses=(
                "l2_active",
                "l2_waiting_user",
                "l2_waiting_approval",
            ),
            status=status,
            stop_reason=stop_reason,
            usage=l2_state.budget,
            final_response=final_response,
            failure_attribution=l2_state.failure_attribution,
        ),
    )


def _stopped_runtime(
    current: L2RuntimeState,
    *,
    reason: L2StopReason,
    message: str,
    failure_attribution: L2FailureAttribution | None = None,
) -> L2RuntimeState:
    """返回包含公开停止原因的不可变 Runtime State。"""

    phase = "budget_exhausted" if reason == "budget_exhausted" else "stopped"
    return current.model_copy(
        update={
            "phase": phase,
            "pending_decision": None,
            "final_response": message,
            "stop_reason": reason,
            "failure_attribution": failure_attribution,
        }
    )


class L2Nodes:
    """封装升级、Agent Loop、记忆审批和退款桥接节点。"""

    def __init__(self, dependencies: Dependencies) -> None:
        """保存窄外部依赖，运行身份仍只从 RunContext 取得。"""

        self._dependencies = dependencies
        self._l2 = _require_l2(dependencies)
        if not isinstance(self._l2.tool_registry, L2ToolRegistry):
            raise TypeError("L2 tool registry is invalid")

    def prepare_upgrade(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """确定性检查能力并生成不产生业务写入的 AI 二线升级预览。"""

        issue_summary = state.get("l2_runtime")
        summary = (
            issue_summary.issue_summary
            if issue_summary is not None
            else str(state.get("error_code") or "复杂售后问题")
        )
        active_case = self._l2.case_repository.get_active_case_for_thread(
            thread_id=runtime.context.task_id or "",
            subject_id=runtime.context.subject_id or runtime.context.user_id,
            user_id=runtime.context.user_id,
            workspace_id=runtime.context.workspace_id,
        )
        decision = decide_l2_upgrade(
            registered=runtime.context.access_mode == "registered",
            llm_allowed=runtime.context.l2_allowed,
            quota_remaining=runtime.context.l2_quota_remaining,
            sale_support_candidate=True,
            has_conflicting_interrupt=False,
            has_active_case=active_case is not None,
        )
        if not decision.allowed:
            stopped = L2RuntimeState(
                phase="stopped",
                issue_summary=summary,
                related_order_id=state.get("order_id"),
                final_response="当前无法进入 AI 深度处理，请稍后重试。",
                stop_reason="safety_rejected",
            )
            return {
                "messages": [{"role": "assistant", "content": stopped.final_response}],
                "status": "l2_stopped",
                "l2_runtime": stopped,
                "l2_upgrade_preview": None,
                "error_code": decision.reason_code,
                "audit": [f"l2_upgrade_rejected:{decision.reason_code}"],
            }
        budget = default_budget()
        preview_id = str(uuid4())
        public_fields: dict[str, object] = {
            "preview_id": preview_id,
            "issue_summary": summary,
            "related_order_id": state.get("order_id"),
            "context_categories": ("conversation", "business_tools", "policy"),
            "allowed_tools": self._l2.tool_registry.names,
            "budget": budget.model_dump(mode="json"),
            "reads_confirmed_preferences": True,
        }
        preview = L2UpgradePreview(
            preview_hash=_preview_hash(public_fields),
            **public_fields,
        )
        l2_state = L2RuntimeState(
            preview_id=preview_id,
            phase="awaiting_confirmation",
            issue_summary=summary,
            related_order_id=state.get("order_id"),
            allowed_tools=preview.allowed_tools,
            budget_limits=budget,
            latest_user_input=summary,
        )
        return {
            "messages": [{"role": "assistant", "content": L2_UPGRADE_MESSAGE}],
            "status": "l2_awaiting_confirmation",
            "l2_upgrade_preview": preview,
            "l2_runtime": l2_state,
            "error_code": None,
            "audit": ["l2_upgrade_preview_created"],
        }

    def await_upgrade_confirmation(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> Command[Literal["l2_cancel_upgrade", "l2_create_case"]]:
        """暂停等待结构化确认，并在恢复时重新检查模型能力与额度。"""

        preview = state.get("l2_upgrade_preview")
        if preview is None:
            raise ValueError("L2 upgrade preview is missing")
        decision = interrupt(
            {
                "type": "l2_upgrade",
                "preview_id": preview.preview_id,
                "preview_hash": preview.preview_hash,
            }
        )
        if not isinstance(decision, dict):
            raise ValueError("L2 upgrade decision is invalid")
        if decision.get("preview_id") != preview.preview_id:
            raise ValueError("L2 upgrade preview does not match")
        if decision.get("decision") == "cancel":
            return Command(goto="l2_cancel_upgrade")
        if decision.get("decision") != "confirm":
            raise ValueError("L2 upgrade decision is invalid")
        if not runtime.context.l2_allowed or runtime.context.l2_quota_remaining <= 0:
            return Command(
                update={"error_code": "l2_model_not_authorized"},
                goto="l2_cancel_upgrade",
            )
        return Command(goto="l2_create_case")

    def cancel_upgrade(self, state: AgentState) -> dict[str, object]:
        """结束未确认升级，保证 Case、Memory 和 L2 模型调用均为零。"""

        current = state.get("l2_runtime")
        if current is None:
            raise ValueError("L2 runtime is missing")
        cancelled = current.model_copy(
            update={
                "phase": "cancelled",
                "final_response": L2_CANCELLED_MESSAGE,
                "stop_reason": "user_cancelled",
            }
        )
        return {
            "messages": [{"role": "assistant", "content": L2_CANCELLED_MESSAGE}],
            "status": "l2_cancelled",
            "l2_runtime": cancelled,
            "audit": ["l2_upgrade_cancelled"],
        }

    def create_case(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """确认后首次幂等创建 Case，并记录不含模型内容的公开事件。"""

        current = state.get("l2_runtime")
        if current is None or current.preview_id is None:
            raise ValueError("creating L2 case requires confirmed preview")
        thread_id = runtime.context.task_id
        if thread_id is None:
            raise ValueError("creating L2 case requires task_id")
        case_id = _stable_id("case", thread_id, current.preview_id)
        active = current.model_copy(
            update={
                "case_id": case_id,
                "phase": "active",
                "context_policy_version": CONTEXT_POLICY_VERSION,
            }
        )
        self._l2.case_repository.create_case_if_absent(
            L2CaseCreate(
                case_id=case_id,
                thread_id=thread_id,
                subject_id=runtime.context.subject_id or runtime.context.user_id,
                user_id=runtime.context.user_id,
                workspace_id=runtime.context.workspace_id,
                related_order_id=current.related_order_id,
                issue_summary=current.issue_summary,
                model_name=self._l2.agent_model.model_name,
                prompt_version=self._l2.prompt_version,
                toolset_version=self._l2.toolset_version,
                context_policy_version=CONTEXT_POLICY_VERSION,
                budget=current.budget_limits,
            )
        )
        _trace(
            self._l2,
            runtime,
            active,
            event_key="case:created",
            event_type="case_created",
            result_code="created",
        )
        return {
            "status": "l2_active",
            "l2_runtime": active,
            "audit": ["l2_case_created"],
        }

    def load_context(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """在确认后读取受限长期偏好摘要并准备第一次模型决策。"""

        current = state.get("l2_runtime")
        if current is None or current.case_id is None:
            raise ValueError("loading L2 context requires case")
        count = (
            len(
                list_preferences(
                    runtime.store,
                    user_id=runtime.context.user_id,
                    workspace_id=runtime.context.workspace_id,
                )
            )
            if runtime.store is not None
            else 0
        )
        _trace(
            self._l2,
            runtime,
            current,
            event_key="case:context-loaded",
            event_type="context_loaded",
            result_code="loaded",
            parameter_summary={"confirmed_preferences": str(count)},
        )
        return {"audit": ["l2_context_loaded"]}

    def decide(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """构建并保存 Context Manifest 后调用一次受预算约束的 L2 模型。"""

        current = state.get("l2_runtime")
        thread_id = runtime.context.task_id
        if current is None or current.case_id is None or thread_id is None:
            raise ValueError("L2 decision requires active case and task")
        next_number = current.budget.model_calls_used + 1
        step_id = _stable_id("step", current.case_id, next_number)
        preparation_started = monotonic()
        observations, change_notes, refresh_count = _refresh_observations(
            self._l2,
            current,
            runtime,
            step_id=step_id,
        )
        preferences = (
            list_preferences(
                runtime.store,
                user_id=runtime.context.user_id,
                workspace_id=runtime.context.workspace_id,
            )
            if runtime.store is not None
            else ()
        )
        messages = (
            self._l2.conversation_reader.list_authorized_context_messages(
                thread_id=thread_id,
                subject_id=runtime.context.subject_id or runtime.context.user_id,
                user_id=runtime.context.user_id,
                workspace_id=runtime.context.workspace_id,
                limit=MAX_MESSAGE_CANDIDATES,
            )
            if self._l2.conversation_reader is not None
            else _state_public_messages(state)
        )
        context_runtime = current.model_copy(update={"observations": observations})
        context_result = build_l2_context(
            runtime=context_runtime,
            case_id=current.case_id,
            step_id=step_id,
            user_id=runtime.context.user_id,
            workspace_id=runtime.context.workspace_id,
            messages=messages,
            preferences=preferences,
            change_notes=change_notes,
            refresh_count=refresh_count,
            now=self._l2.clock(),
        )
        preparation_ms = max(0, int((monotonic() - preparation_started) * 1000))
        manifest = context_result.manifest.model_copy(
            update={"context_preparation_ms": preparation_ms}
        )
        try:
            self._l2.case_repository.save_manifest_once(
                user_id=runtime.context.user_id,
                workspace_id=runtime.context.workspace_id,
                manifest=manifest,
            )
        except (ValueError, RuntimeError):
            stopped = _stopped_runtime(
                context_runtime,
                reason="insufficient_evidence",
                message=public_failure_message("context_missing"),
                failure_attribution="context_missing",
            )
            return {"l2_runtime": stopped, "audit": ["l2:manifest_failed"]}
        selected_evidence = (
            manifest.public_summary.public_evidence_ids
            if context_result.pack is not None
            else ()
        )
        prepared = context_runtime.model_copy(
            update={
                "context_policy_version": CONTEXT_POLICY_VERSION,
                "last_context_manifest_id": manifest.manifest_id,
                "last_context_evidence_ids": selected_evidence,
                "failure_attribution": context_result.failure_attribution,
            }
        )
        _trace(
            self._l2,
            runtime,
            prepared,
            event_key=f"{step_id}:context",
            event_type="context_prepared",
            result_code=(context_result.failure_attribution or "ready"),
            evidence_ids=manifest.public_summary.public_evidence_ids,
            duration_ms=preparation_ms,
            context_summary=manifest.public_summary,
        )
        if not context_result.ready or context_result.pack is None:
            attribution = context_result.failure_attribution or "context_missing"
            stopped = _stopped_runtime(
                prepared,
                reason="insufficient_evidence",
                message=public_failure_message(attribution),
                failure_attribution=attribution,
            )
            return {"l2_runtime": stopped, "audit": [f"l2:{attribution}"]}
        request = L2ModelRequest(
            case_id=current.case_id,
            step_id=step_id,
            context_policy_version=CONTEXT_POLICY_VERSION,
            context=context_result.pack,
        )
        projected_tokens = estimate_total_tokens(request.model_dump_json())
        budget_error = check_model_budget(prepared, projected_tokens=projected_tokens)
        if budget_error is not None:
            stopped = _stopped_runtime(
                prepared,
                reason="budget_exhausted",
                message=public_failure_message("budget_exhausted"),
                failure_attribution="budget_exhausted",
            )
            return {"l2_runtime": stopped, "audit": [f"l2:{budget_error}"]}
        call_id = _stable_id("call", current.case_id, next_number)
        started = self._l2.case_repository.begin_model_call(
            data=L2ModelCallStart(
                call_id=call_id,
                user_id=runtime.context.user_id,
                thread_id=thread_id,
                case_id=current.case_id,
                step_id=step_id,
                model_name=self._l2.agent_model.model_name,
                manifest_id=manifest.manifest_id,
                charged_tokens=projected_tokens,
                created_at=self._l2.clock(),
            ),
            usage_date=runtime.context.as_of or date.today(),
            daily_limit=self._l2.daily_call_limit,
        )
        if started is None:
            stopped = _stopped_runtime(
                prepared,
                reason="budget_exhausted",
                message=public_failure_message("budget_exhausted"),
                failure_attribution="budget_exhausted",
            )
            return {"l2_runtime": stopped, "audit": ["l2:model_quota_exhausted"]}
        call_started = monotonic()
        try:
            result = self._l2.agent_model.decide(request)
        except L2ModelUnavailableError:
            duration_ms = max(0, int((monotonic() - call_started) * 1000))
            self._l2.case_repository.finish_model_call(
                call_id=call_id,
                user_id=runtime.context.user_id,
                case_id=current.case_id,
                status="failed",
                input_tokens=0,
                output_tokens=0,
                duration_ms=duration_ms,
                usage_source="unknown",
            )
            budget = budget_after_model_call(
                prepared.budget,
                charged_tokens=projected_tokens,
                duration_ms=duration_ms,
            )
            stopped = _stopped_runtime(
                prepared.model_copy(update={"budget": budget}),
                reason="model_unavailable",
                message=L2_MODEL_UNAVAILABLE_MESSAGE,
                failure_attribution="model_unavailable",
            )
            return {"l2_runtime": stopped, "audit": ["l2:model_unavailable"]}
        except L2ModelOutputInvalidError:
            duration_ms = max(0, int((monotonic() - call_started) * 1000))
            self._l2.case_repository.finish_model_call(
                call_id=call_id,
                user_id=runtime.context.user_id,
                case_id=current.case_id,
                status="failed",
                input_tokens=0,
                output_tokens=0,
                duration_ms=duration_ms,
                usage_source="unknown",
            )
            budget = budget_after_model_call(
                prepared.budget,
                charged_tokens=projected_tokens,
                duration_ms=duration_ms,
            )
            stopped = _stopped_runtime(
                prepared.model_copy(update={"budget": budget}),
                reason="invalid_model_output",
                message=public_failure_message("model_output_invalid"),
                failure_attribution="model_output_invalid",
            )
            return {"l2_runtime": stopped, "audit": ["l2:model_output_invalid"]}
        duration_ms = max(0, int((monotonic() - call_started) * 1000))
        self._l2.case_repository.finish_model_call(
            call_id=call_id,
            user_id=runtime.context.user_id,
            case_id=current.case_id,
            status="completed",
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            duration_ms=duration_ms,
            usage_source="estimated" if result.usage.estimated else "provider",
        )
        budget = budget_after_model_call(
            prepared.budget,
            charged_tokens=projected_tokens,
            duration_ms=duration_ms,
        )
        updated = prepared.model_copy(
            update={
                "budget": budget,
                "pending_decision": result.decision,
                "failure_attribution": None,
            }
        )
        _transition(self._l2, runtime, updated, status="l2_active")
        _trace(
            self._l2,
            runtime,
            updated,
            event_key=f"{step_id}:decision",
            event_type="model_decision",
            result_code=result.decision.kind,
            duration_ms=duration_ms,
        )
        return {
            "l2_runtime": updated,
            "status": "l2_active",
            "audit": [f"l2_decision:{result.decision.kind}"],
        }

    def validate_decision(self, state: AgentState) -> dict[str, object]:
        """确定性验证模型候选动作并准备对应公开交互。"""

        current = state.get("l2_runtime")
        if current is None:
            raise ValueError("validating L2 decision requires runtime")
        decision = current.pending_decision
        if decision is None:
            return {"l2_runtime": current}
        if isinstance(decision, ToolCallDecision):
            rejection = validate_tool_call(current, decision.call)
            if rejection is not None:
                reason: L2StopReason = (
                    "no_progress" if rejection == "no_progress" else "budget_exhausted"
                )
                stopped = _stopped_runtime(
                    current,
                    reason=reason,
                    message="AI 深度处理无法在安全预算内继续，任务已停止。",
                    failure_attribution=(
                        "tool_rejected"
                        if reason == "no_progress"
                        else "budget_exhausted"
                    ),
                )
                return {"l2_runtime": stopped, "audit": [f"l2:{rejection}"]}
            return {"l2_runtime": current}
        if isinstance(decision, AskUserDecision):
            waiting = current.model_copy(
                update={
                    "phase": "waiting_user",
                    "failure_attribution": "user_input_required",
                }
            )
            return {
                "messages": [{"role": "assistant", "content": decision.question}],
                "status": "l2_waiting_user",
                "l2_runtime": waiting,
                "audit": ["l2_waiting_user"],
            }
        if isinstance(decision, ProposeMemoryDecision):
            if current.case_id is None:
                raise ValueError("memory proposal requires case")
            proposal = MemoryProposal(
                proposal_id=_stable_id(
                    "memory",
                    current.case_id,
                    current.budget.steps_used,
                    decision.memory_type,
                ),
                case_id=current.case_id,
                memory_type=decision.memory_type,
                value=decision.value,
                purpose=decision.purpose,
            )
            waiting = current.model_copy(
                update={
                    "phase": "waiting_memory_confirmation",
                    "pending_memory_proposal": proposal,
                }
            )
            message = (
                f"AI 深度处理建议保存偏好：{proposal.memory_type}={proposal.value}。"
                "只有你明确确认后才会写入长期记忆。"
            )
            return {
                "messages": [{"role": "assistant", "content": message}],
                "status": "l2_waiting_memory_confirmation",
                "l2_runtime": waiting,
                "audit": ["l2_memory_proposed"],
            }
        if isinstance(decision, AnswerDecision):
            available = set(current.last_context_evidence_ids)
            if not decision.evidence_ids or not set(decision.evidence_ids) <= available:
                unresolved = current.model_copy(
                    update={
                        "phase": "unresolved",
                        "final_response": "现有可信证据不足，AI 深度处理未生成结论。",
                        "stop_reason": "insufficient_evidence",
                        "failure_attribution": "verification_failed",
                    }
                )
                return {"l2_runtime": unresolved, "audit": ["l2:evidence_rejected"]}
            resolved = current.model_copy(
                update={
                    "phase": "resolved",
                    "final_response": decision.answer,
                    "stop_reason": "resolved",
                    "failure_attribution": None,
                }
            )
            return {"l2_runtime": resolved}
        if isinstance(decision, StopDecision):
            reason: L2StopReason = (
                "budget_exhausted"
                if decision.reason == "model_limit"
                else cast(L2StopReason, decision.reason)
            )
            stopped = current.model_copy(
                update={
                    "phase": "unresolved",
                    "final_response": decision.public_message,
                    "stop_reason": reason,
                }
            )
            return {"l2_runtime": stopped}
        if isinstance(decision, ProposeRefundDecision):
            waiting = current.model_copy(update={"phase": "waiting_refund_approval"})
            return {"l2_runtime": waiting}
        stopped = _stopped_runtime(
            current,
            reason="invalid_model_output",
            message="AI 深度处理返回了无效动作，当前任务已安全停止。",
            failure_attribution="model_output_invalid",
        )
        return {"l2_runtime": stopped}

    def execute_tool(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """执行已验证的单个 R0 工具，并保存有界 Observation 与预算。"""

        current = state.get("l2_runtime")
        if current is None or not isinstance(
            current.pending_decision,
            ToolCallDecision,
        ):
            raise ValueError("executing L2 tool requires tool decision")
        decision = current.pending_decision
        signature = tool_action_signature(decision.call)
        observation, duration_ms = self._l2.tool_registry.execute(
            decision.call,
            L2ToolContext(
                scope=_scope(runtime),
                as_of=runtime.context.as_of or date.today(),
                step_id=_stable_id(
                    "tool-step",
                    current.case_id,
                    current.budget.tool_calls_used + 1,
                ),
                dependencies=self._dependencies,
                store=runtime.store,
            ),
            now=self._l2.clock(),
        )
        succeeded = observation.result_code in {"found", "insufficient_evidence"}
        budget = budget_after_tool_call(
            current.budget,
            signature=signature,
            succeeded=succeeded,
            duration_ms=duration_ms,
        )
        updated = current.model_copy(
            update={
                "phase": "active",
                "observations": (*current.observations, observation)[-20:],
                "pending_decision": None,
                "budget": budget,
            }
        )
        if (
            budget.consecutive_tool_failures
            >= current.budget_limits.max_consecutive_tool_failures
        ):
            updated = _stopped_runtime(
                updated,
                reason="tool_failed",
                message="受控业务工具连续失败，AI 深度处理已安全停止。",
                failure_attribution="tool_failed",
            )
        _transition(self._l2, runtime, updated, status="l2_active")
        parameters = (
            {"order_id": decision.call.order_id}
            if hasattr(decision.call, "order_id")
            else {"topic": decision.call.query.topic}
            if hasattr(decision.call, "query")
            else None
        )
        _trace(
            self._l2,
            runtime,
            updated,
            event_key=f"{observation.step_id}:tool",
            event_type="tool_result",
            result_code=observation.result_code,
            tool_category=decision.call.tool,
            parameter_summary=parameters,
            evidence_ids=observation.evidence_ids,
            duration_ms=duration_ms,
        )
        return {
            "l2_runtime": updated,
            "status": "l2_active",
            "audit": [f"l2_tool:{decision.call.tool}:{observation.result_code}"],
        }

    def await_user_input(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """暂停等待聊天框补充信息，恢复时不再经过一线 Interpreter。"""

        current = state.get("l2_runtime")
        if current is None or not isinstance(current.pending_decision, AskUserDecision):
            raise ValueError("waiting user input requires ask_user decision")
        _transition(self._l2, runtime, current, status="l2_waiting_user")
        resumed = interrupt(
            {
                "type": "l2_user_input",
                "case_id": current.case_id,
                "question": current.pending_decision.question,
            }
        )
        if not isinstance(resumed, dict) or not isinstance(resumed.get("message"), str):
            raise ValueError("L2 user input is invalid")
        message = resumed["message"].strip()
        if not message or len(message) > 2000:
            raise ValueError("L2 user input length is invalid")
        active = current.model_copy(
            update={
                "phase": "active",
                "latest_user_input": message,
                "pending_decision": None,
                "failure_attribution": None,
            }
        )
        _transition(self._l2, runtime, active, status="l2_active")
        _trace(
            self._l2,
            runtime,
            active,
            event_key=f"case:user-input:{current.budget.steps_used}",
            event_type="user_input_received",
            result_code="received",
        )
        return {
            "messages": [{"role": "user", "content": message}],
            "status": "l2_active",
            "l2_runtime": active,
            "audit": ["l2_user_input_received"],
        }

    def await_memory_confirmation(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """暂停等待受限偏好确认，确认后幂等写入并回读验证。"""

        current = state.get("l2_runtime")
        proposal = current.pending_memory_proposal if current is not None else None
        if current is None or proposal is None:
            raise ValueError("waiting memory confirmation requires proposal")
        _transition(self._l2, runtime, current, status="l2_waiting_approval")
        resumed = interrupt(
            {
                "type": "l2_memory",
                "proposal_id": proposal.proposal_id,
                "memory_type": proposal.memory_type,
                "value": proposal.value,
                "purpose": proposal.purpose,
            }
        )
        if not isinstance(resumed, dict):
            raise ValueError("L2 memory decision is invalid")
        if resumed.get("proposal_id") != proposal.proposal_id:
            raise ValueError("L2 memory proposal does not match")
        decision = resumed.get("decision")
        if decision not in {"confirm", "reject"}:
            raise ValueError("L2 memory decision is invalid")
        if decision == "confirm":
            if runtime.store is None:
                raise ValueError("L2 memory store is unavailable")
            saved = confirm_preference(
                runtime.store,
                user_id=runtime.context.user_id,
                workspace_id=runtime.context.workspace_id,
                proposal=proposal,
                now=self._l2.clock(),
            )
            result_code = "confirmed"
            evidence_ids = (f"memory:{saved.memory_id}",)
            summary = f"用户已确认偏好 {saved.memory_type}={saved.value}。"
        else:
            result_code = "rejected"
            evidence_ids = ()
            summary = "用户拒绝保存该长期偏好。"
        observation = L2Observation(
            observation_id=str(uuid4()),
            step_id=_stable_id("memory-step", proposal.proposal_id),
            source_type="memory_confirmation",
            source_ref=proposal.proposal_id,
            result_code=result_code,
            summary=summary,
            evidence_ids=evidence_ids,
            observed_at=self._l2.clock(),
        )
        active = current.model_copy(
            update={
                "phase": "active",
                "pending_decision": None,
                "pending_memory_proposal": None,
                "observations": (*current.observations, observation)[-20:],
            }
        )
        _transition(self._l2, runtime, active, status="l2_active")
        _trace(
            self._l2,
            runtime,
            active,
            event_key=f"memory:{proposal.proposal_id}",
            event_type="memory_decision",
            result_code=result_code,
            evidence_ids=evidence_ids,
        )
        return {
            "status": "l2_active",
            "l2_runtime": active,
            "audit": [f"l2_memory:{result_code}"],
        }

    def bridge_refund(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """仅传递订单和原因到既有 v0.4 流程，不携带金额或批准结论。"""

        current = state.get("l2_runtime")
        if current is None or not isinstance(
            current.pending_decision, ProposeRefundDecision
        ):
            raise ValueError("bridging refund requires refund proposal")
        decision = current.pending_decision
        waiting = current.model_copy(update={"phase": "waiting_refund_approval"})
        _transition(self._l2, runtime, waiting, status="l2_waiting_approval")
        _trace(
            self._l2,
            runtime,
            waiting,
            event_key=f"refund:{current.budget.steps_used}:proposed",
            event_type="refund_proposed",
            result_code="candidate_only",
            parameter_summary={"order_id": decision.order_id},
        )
        return {
            "order_id": decision.order_id,
            "refund_reason": decision.reason,
            "pending_refund_request": True,
            "l2_runtime": waiting,
            "audit": ["l2_refund_bridged"],
        }

    def record_refund_result(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """把现有退款终态转换为只读 Observation 后返回 L2 Loop。"""

        current = state.get("l2_runtime")
        if current is None or current.case_id is None:
            raise ValueError("recording refund result requires L2 case")
        status = str(state.get("status", "refund_failed"))
        verification = state.get("refund_verification")
        evidence_ids = (
            (f"refund:{verification.refund_id}:{verification.status}",)
            if verification is not None and verification.refund_id is not None
            else ()
        )
        observation = L2Observation(
            observation_id=str(uuid4()),
            step_id=_stable_id(
                "refund-step",
                current.case_id,
                current.budget.steps_used,
            ),
            source_type="refund_workflow",
            source_ref=state.get("order_id") or "unknown-order",
            result_code=status,
            summary=f"既有退款流程返回状态：{status}。",
            evidence_ids=evidence_ids,
            observed_at=self._l2.clock(),
        )
        active = current.model_copy(
            update={
                "phase": "active",
                "pending_decision": None,
                "observations": (*current.observations, observation)[-20:],
            }
        )
        _transition(self._l2, runtime, active, status="l2_active")
        _trace(
            self._l2,
            runtime,
            active,
            event_key=f"refund:{current.budget.steps_used}:result",
            event_type="refund_result",
            result_code=status,
            evidence_ids=evidence_ids,
        )
        return {
            "l2_runtime": active,
            "status": "l2_active",
            "audit": [f"l2_refund_observed:{status}"],
        }

    def finalize_resolved(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """保存已证据约束的最终回答并结束 L2 Case。"""

        current = state.get("l2_runtime")
        if current is None or not current.final_response:
            raise ValueError("resolving L2 case requires final response")
        _transition(
            self._l2,
            runtime,
            current,
            status="l2_resolved",
            stop_reason="resolved",
            final_response=current.final_response,
        )
        _trace(
            self._l2,
            runtime,
            current,
            event_key="case:resolved",
            event_type="case_completed",
            result_code="resolved",
        )
        return {
            "messages": [{"role": "assistant", "content": current.final_response}],
            "status": "l2_resolved",
            "audit": ["l2_resolved"],
        }

    def finalize_stopped(
        self,
        state: AgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, object]:
        """以明确公开原因终止未解决、失败或预算耗尽的 L2 Case。"""

        current = state.get("l2_runtime")
        if current is None or current.case_id is None:
            raise ValueError("stopping L2 case requires case")
        message = current.final_response or "AI 深度处理未能安全完成当前任务。"
        if current.phase == "budget_exhausted":
            case_status: L2CaseStatus = "l2_budget_exhausted"
            public_status = "l2_budget_exhausted"
        elif current.phase == "unresolved":
            case_status = "l2_unresolved"
            public_status = "l2_unresolved"
        else:
            case_status = "l2_stopped"
            public_status = "l2_stopped"
        reason = current.stop_reason or "unsupported"
        _transition(
            self._l2,
            runtime,
            current,
            status=case_status,
            stop_reason=reason,
            final_response=message,
        )
        _trace(
            self._l2,
            runtime,
            current,
            event_key="case:stopped",
            event_type="case_completed",
            result_code=reason,
        )
        return {
            "messages": [{"role": "assistant", "content": message}],
            "status": public_status,
            "audit": [f"l2_stopped:{reason}"],
        }


def route_after_upgrade(
    state: AgentState,
) -> Literal["l2_await_upgrade_confirmation", "__end__"]:
    """只有成功生成预览时才允许进入确认中断。"""

    return (
        "l2_await_upgrade_confirmation"
        if state.get("l2_upgrade_preview") is not None
        else END
    )


def route_after_decide(
    state: AgentState,
) -> Literal["l2_validate_decision", "l2_finalize_stopped"]:
    """模型产生候选动作时继续校验，否则进入安全停止。"""

    current = state.get("l2_runtime")
    return (
        "l2_validate_decision"
        if current is not None and current.pending_decision is not None
        else "l2_finalize_stopped"
    )


def route_after_validation(
    state: AgentState,
) -> Literal[
    "l2_execute_tool",
    "l2_await_user_input",
    "l2_await_memory_confirmation",
    "l2_bridge_refund",
    "l2_finalize_resolved",
    "l2_finalize_stopped",
]:
    """根据经过确定性校验的决策类型选择唯一下一节点。"""

    current = state.get("l2_runtime")
    if current is None:
        return "l2_finalize_stopped"
    if current.phase == "resolved":
        return "l2_finalize_resolved"
    if current.phase in {"unresolved", "stopped", "budget_exhausted"}:
        return "l2_finalize_stopped"
    decision = current.pending_decision
    if isinstance(decision, ToolCallDecision):
        return "l2_execute_tool"
    if isinstance(decision, AskUserDecision):
        return "l2_await_user_input"
    if isinstance(decision, ProposeMemoryDecision):
        return "l2_await_memory_confirmation"
    if isinstance(decision, ProposeRefundDecision):
        return "l2_bridge_refund"
    return "l2_finalize_stopped"


def route_after_tool(
    state: AgentState,
) -> Literal["l2_decide", "l2_finalize_stopped"]:
    """工具成功或可解释空结果时继续，连续失败时安全停止。"""

    current = state.get("l2_runtime")
    return (
        "l2_decide"
        if current is not None and current.phase == "active"
        else "l2_finalize_stopped"
    )


def register_l2_workflow(builder: StateGraph, dependencies: Dependencies) -> None:
    """把 L2 Harness 节点注册到现有唯一主图，不创建嵌套子图。"""

    nodes = L2Nodes(dependencies)
    builder.add_node("l2_prepare_upgrade", nodes.prepare_upgrade)
    builder.add_node(
        "l2_await_upgrade_confirmation",
        nodes.await_upgrade_confirmation,
    )
    builder.add_node("l2_cancel_upgrade", nodes.cancel_upgrade)
    builder.add_node("l2_create_case", nodes.create_case)
    builder.add_node("l2_load_context", nodes.load_context)
    builder.add_node("l2_decide", nodes.decide)
    builder.add_node("l2_validate_decision", nodes.validate_decision)
    builder.add_node("l2_execute_tool", nodes.execute_tool)
    builder.add_node("l2_await_user_input", nodes.await_user_input)
    builder.add_node(
        "l2_await_memory_confirmation",
        nodes.await_memory_confirmation,
    )
    builder.add_node("l2_bridge_refund", nodes.bridge_refund)
    builder.add_node("l2_record_refund_result", nodes.record_refund_result)
    builder.add_node("l2_finalize_resolved", nodes.finalize_resolved)
    builder.add_node("l2_finalize_stopped", nodes.finalize_stopped)
    builder.add_conditional_edges("l2_prepare_upgrade", route_after_upgrade)
    builder.add_edge("l2_cancel_upgrade", END)
    builder.add_edge("l2_create_case", "l2_load_context")
    builder.add_edge("l2_load_context", "l2_decide")
    builder.add_conditional_edges("l2_decide", route_after_decide)
    builder.add_conditional_edges("l2_validate_decision", route_after_validation)
    builder.add_conditional_edges("l2_execute_tool", route_after_tool)
    builder.add_edge("l2_await_user_input", "l2_decide")
    builder.add_edge("l2_await_memory_confirmation", "l2_decide")
    builder.add_edge("l2_bridge_refund", "prepare_refund_request")
    builder.add_edge("l2_record_refund_result", "l2_decide")
    builder.add_edge("l2_finalize_resolved", END)
    builder.add_edge("l2_finalize_stopped", END)
