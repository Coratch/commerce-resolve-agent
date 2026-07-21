"""实现 L2 Support Case、公开 Trace 与模型调用计量的 SQLite Repository。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.business_models import LlmUsageRecord
from commerce_resolve.l2_models import (
    L2BudgetLimits,
    L2BudgetState,
    L2CaseCreate,
    L2CaseMetrics,
    L2CaseRecord,
    L2CaseStatus,
    L2CaseTransition,
    L2ContextManifest,
    L2ContextManifestItem,
    L2ContextPublicSummary,
    L2FailureAttribution,
    L2ModelCallRecord,
    L2ModelCallStart,
    L2PublicTraceEvent,
    L2StopReason,
    L2ToolName,
    L2TraceState,
    L2UsageSource,
)

from .sqlalchemy_models import (
    L2CaseEventRow,
    L2ContextManifestRow,
    L2SupportCaseRow,
    LlmCallEventRow,
    LlmDailyUsageRow,
    utc_now,
)
from .sqlite_business import BusinessDataError

TERMINAL_CASE_STATUSES = {
    "l2_resolved",
    "l2_unresolved",
    "l2_budget_exhausted",
    "l2_cancelled",
    "l2_stopped",
}


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqliteL2CaseRepository:
    """在业务数据库内持久化 L2 Case、公开事件和模型用量。"""

    def __init__(
        self,
        engine: Engine,
        *,
        now_provider: Callable[[], datetime] = utc_now,
    ) -> None:
        """保存共享 Engine 和可替换时钟，每个方法使用独立短事务。"""

        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._now = now_provider

    def _now_utc(self) -> datetime:
        """读取并规范化当前 UTC 时间。"""

        return _as_utc(self._now())

    def create_case_if_absent(self, data: L2CaseCreate) -> L2CaseRecord:
        """幂等创建活动 Case，并阻止同一 thread 同时存在第二个活动 Case。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            existing = session.get(L2SupportCaseRow, data.case_id)
            if existing is not None:
                if (
                    existing.thread_id != data.thread_id
                    or existing.subject_id != data.subject_id
                    or existing.user_id != data.user_id
                    or existing.workspace_id != data.workspace_id
                ):
                    raise BusinessDataError("l2_case_conflict")
                return self._case_record(existing)
            row = L2SupportCaseRow(
                case_id=data.case_id,
                thread_id=data.thread_id,
                subject_id=data.subject_id,
                user_id=data.user_id,
                workspace_id=data.workspace_id,
                related_order_id=data.related_order_id,
                issue_summary=data.issue_summary,
                status="l2_active",
                model_name=data.model_name,
                prompt_version=data.prompt_version,
                toolset_version=data.toolset_version,
                context_policy_version=data.context_policy_version,
                trace_state=(
                    "complete" if data.context_policy_version is not None else "partial"
                ),
                next_event_sequence=1,
                max_steps=data.budget.max_steps,
                max_model_calls=data.budget.max_model_calls,
                max_tool_calls=data.budget.max_tool_calls,
                max_estimated_tokens=data.budget.max_estimated_tokens,
                max_active_milliseconds=data.budget.max_active_milliseconds,
                max_invocation_milliseconds=data.budget.max_invocation_milliseconds,
                max_consecutive_tool_failures=(
                    data.budget.max_consecutive_tool_failures
                ),
                steps_used=0,
                model_calls_used=0,
                tool_calls_used=0,
                estimated_tokens_used=0,
                active_milliseconds=0,
                consecutive_tool_failures=0,
                repeated_action_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as error:
                raise BusinessDataError("l2_case_conflict") from error
            return self._case_record(row)

    def get_authorized_case(
        self,
        *,
        case_id: str,
        subject_id: str,
        user_id: str,
        workspace_id: str,
        thread_id: str | None = None,
    ) -> L2CaseRecord | None:
        """按完整可信作用域读取 Case，不泄露其他用户资源。"""

        with self._sessions() as session:
            statement = select(L2SupportCaseRow).where(
                L2SupportCaseRow.case_id == case_id,
                L2SupportCaseRow.subject_id == subject_id,
                L2SupportCaseRow.user_id == user_id,
                L2SupportCaseRow.workspace_id == workspace_id,
            )
            if thread_id is not None:
                statement = statement.where(L2SupportCaseRow.thread_id == thread_id)
            row = session.scalar(statement)
            return self._case_record_for_read(session, row) if row is not None else None

    def get_active_case_for_thread(
        self,
        *,
        thread_id: str,
        subject_id: str,
        user_id: str,
        workspace_id: str,
    ) -> L2CaseRecord | None:
        """读取当前 thread 唯一活动或等待中的 L2 Case。"""

        with self._sessions() as session:
            row = session.scalar(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.thread_id == thread_id,
                    L2SupportCaseRow.subject_id == subject_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                    L2SupportCaseRow.status.in_(
                        ("l2_active", "l2_waiting_user", "l2_waiting_approval")
                    ),
                )
            )
            return self._case_record_for_read(session, row) if row is not None else None

    def list_authorized_cases(
        self,
        *,
        subject_id: str,
        user_id: str,
        workspace_id: str,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> tuple[L2CaseRecord, ...]:
        """按最近更新时间列出当前账号的有限 Case 摘要。"""

        safe_limit = max(1, min(limit, 100))
        with self._sessions() as session:
            statement = select(L2SupportCaseRow).where(
                L2SupportCaseRow.subject_id == subject_id,
                L2SupportCaseRow.user_id == user_id,
                L2SupportCaseRow.workspace_id == workspace_id,
            )
            if thread_id is not None:
                statement = statement.where(L2SupportCaseRow.thread_id == thread_id)
            rows = session.scalars(
                statement.order_by(L2SupportCaseRow.updated_at.desc()).limit(safe_limit)
            ).all()
            return tuple(self._case_record_for_read(session, row) for row in rows)

    def transition_case(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
        transition: L2CaseTransition,
    ) -> L2CaseRecord:
        """按期望状态幂等迁移 Case，并保存不倒退的预算用量。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            row = session.scalar(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.case_id == case_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise BusinessDataError("l2_case_not_accessible")
            if row.status != transition.status and row.status not in set(
                transition.expected_statuses
            ):
                raise BusinessDataError("l2_case_state_conflict")
            usage = transition.usage
            row.status = transition.status
            row.stop_reason = transition.stop_reason
            row.steps_used = max(row.steps_used, usage.steps_used)
            row.model_calls_used = max(row.model_calls_used, usage.model_calls_used)
            row.tool_calls_used = max(row.tool_calls_used, usage.tool_calls_used)
            row.estimated_tokens_used = max(
                row.estimated_tokens_used,
                usage.estimated_tokens_used,
            )
            row.active_milliseconds = max(
                row.active_milliseconds,
                usage.active_milliseconds,
            )
            row.consecutive_tool_failures = usage.consecutive_tool_failures
            row.last_action_signature = usage.last_action_signature
            row.repeated_action_count = usage.repeated_action_count
            row.final_response = transition.final_response
            row.failure_attribution = transition.failure_attribution
            row.updated_at = now
            if transition.status in TERMINAL_CASE_STATUSES:
                row.completed_at = row.completed_at or now
            session.flush()
            return self._case_record(row)

    def append_event_once(
        self,
        *,
        user_id: str,
        workspace_id: str,
        event: L2PublicTraceEvent,
    ) -> L2PublicTraceEvent:
        """按 Case/event key 幂等保存脱敏公开事件。"""

        with self._sessions.begin() as session:
            case = session.scalar(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.case_id == event.case_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            )
            if case is None:
                raise BusinessDataError("l2_case_not_accessible")
            existing = session.scalar(
                select(L2CaseEventRow).where(
                    L2CaseEventRow.case_id == event.case_id,
                    L2CaseEventRow.event_key == event.event_key,
                )
            )
            if existing is not None:
                return self._event_record(existing)
            row = L2CaseEventRow(
                event_id=event.event_id,
                case_id=event.case_id,
                event_key=event.event_key,
                sequence_no=case.next_event_sequence,
                payload_version=event.payload_version,
                step_number=event.step_number,
                event_type=event.event_type,
                tool_category=event.tool_category,
                risk=event.risk,
                parameter_summary_json=(
                    json.dumps(
                        event.parameter_summary,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if event.parameter_summary is not None
                    else None
                ),
                result_code=event.result_code,
                evidence_refs_json=json.dumps(
                    event.evidence_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                duration_ms=event.duration_ms,
                context_summary_json=(
                    event.context_summary.model_dump_json(exclude_none=True)
                    if event.context_summary is not None
                    else None
                ),
                created_at=event.created_at,
            )
            case.next_event_sequence += 1
            session.add(row)
            session.flush()
            return self._event_record(row)

    def list_events(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[L2PublicTraceEvent, ...]:
        """按步骤顺序列出当前账号 Case 的公开 Trace。"""

        safe_limit = max(1, min(limit, 200))
        with self._sessions() as session:
            case_exists = session.scalar(
                select(L2SupportCaseRow.case_id).where(
                    L2SupportCaseRow.case_id == case_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            )
            if case_exists is None:
                return ()
            rows = session.scalars(
                select(L2CaseEventRow)
                .where(
                    L2CaseEventRow.case_id == case_id,
                    L2CaseEventRow.sequence_no > max(0, after_sequence),
                )
                .order_by(L2CaseEventRow.sequence_no.asc())
                .limit(safe_limit)
            ).all()
            return tuple(self._event_record(row) for row in rows)

    def begin_model_call(
        self,
        *,
        data: L2ModelCallStart,
        usage_date: date,
        daily_limit: int,
    ) -> L2ModelCallRecord | None:
        """原子占用每日与 Case 预算并登记一次真实 L2 Provider 尝试。"""

        if daily_limit <= 0:
            return None
        with self._sessions.begin() as session:
            existing = session.get(LlmCallEventRow, data.call_id)
            if existing is not None:
                return self._model_call_record(existing)
            case = session.scalar(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.case_id == data.case_id,
                    L2SupportCaseRow.user_id == data.user_id,
                    L2SupportCaseRow.thread_id == data.thread_id,
                )
            )
            if case is None or case.status != "l2_active":
                return None
            if case.context_policy_version is not None:
                if data.manifest_id is None:
                    return None
                manifest = session.scalar(
                    select(L2ContextManifestRow).where(
                        L2ContextManifestRow.manifest_id == data.manifest_id,
                        L2ContextManifestRow.case_id == data.case_id,
                        L2ContextManifestRow.step_id == data.step_id,
                    )
                )
                if manifest is None:
                    return None
            if (
                case.model_calls_used >= case.max_model_calls
                or case.estimated_tokens_used + data.charged_tokens
                > case.max_estimated_tokens
            ):
                return None
            statement = sqlite_insert(LlmDailyUsageRow).values(
                user_id=data.user_id,
                usage_date=usage_date,
                accepted_calls=1,
            )
            statement = statement.on_conflict_do_update(
                index_elements=("user_id", "usage_date"),
                set_={"accepted_calls": LlmDailyUsageRow.accepted_calls + 1},
                where=LlmDailyUsageRow.accepted_calls < daily_limit,
            )
            accepted = session.execute(statement)
            if accepted.rowcount != 1:
                return None
            row = LlmCallEventRow(
                call_id=data.call_id,
                user_id=data.user_id,
                usage_date=usage_date,
                feature="l2_agent",
                thread_id=data.thread_id,
                case_id=data.case_id,
                step_id=data.step_id,
                model_name=data.model_name,
                manifest_id=data.manifest_id,
                status="started",
                input_tokens=0,
                output_tokens=0,
                charged_tokens=data.charged_tokens,
                duration_ms=0,
                usage_source="unknown",
                created_at=data.created_at,
            )
            case.model_calls_used += 1
            case.estimated_tokens_used += data.charged_tokens
            case.updated_at = self._now_utc()
            session.add(row)
            session.flush()
            return self._model_call_record(row)

    def finish_model_call(
        self,
        *,
        call_id: str,
        user_id: str,
        case_id: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        usage_source: L2UsageSource = "unknown",
    ) -> L2ModelCallRecord | None:
        """幂等完成或失败一次模型计量，不重复增加每日额度。"""

        if status not in {"completed", "failed"}:
            raise ValueError("model call status must be completed or failed")
        with self._sessions.begin() as session:
            row = session.scalar(
                select(LlmCallEventRow).where(
                    LlmCallEventRow.call_id == call_id,
                    LlmCallEventRow.user_id == user_id,
                    LlmCallEventRow.case_id == case_id,
                )
            )
            if row is None:
                return None
            if row.status == "started":
                row.status = status
                row.input_tokens = max(0, input_tokens)
                row.output_tokens = max(0, output_tokens)
                row.duration_ms = max(0, duration_ms)
                row.usage_source = usage_source
                row.completed_at = self._now_utc()
            session.flush()
            return self._model_call_record(row)

    def get_llm_usage(self, user_id: str, usage_date: date) -> LlmUsageRecord:
        """读取包含一线与 L2 调用在内的当日聚合额度。"""

        with self._sessions() as session:
            row = session.get(LlmDailyUsageRow, (user_id, usage_date))
            return LlmUsageRecord(
                user_id=user_id,
                usage_date=usage_date,
                accepted_calls=row.accepted_calls if row is not None else 0,
            )

    def count_cases(self) -> int:
        """返回 L2 Case 总数，供确认前零副作用测试使用。"""

        with self._sessions() as session:
            return int(
                session.scalar(select(func.count()).select_from(L2SupportCaseRow)) or 0
            )

    def count_events(self) -> int:
        """返回 L2 公开事件总数，供幂等测试使用。"""

        with self._sessions() as session:
            return int(
                session.scalar(select(func.count()).select_from(L2CaseEventRow)) or 0
            )

    def count_model_calls(self) -> int:
        """返回真实 L2 Provider 尝试记录数。"""

        with self._sessions() as session:
            return int(
                session.scalar(select(func.count()).select_from(LlmCallEventRow)) or 0
            )

    def save_manifest_once(
        self,
        *,
        user_id: str,
        workspace_id: str,
        manifest: L2ContextManifest,
    ) -> L2ContextManifest:
        """在 Provider 调用前幂等保存无正文 Manifest，冲突时明确失败。"""

        public_json = manifest.public_summary.model_dump_json(exclude_none=True)
        items_json = json.dumps(
            [
                item.model_dump(mode="json", exclude_none=True)
                for item in manifest.items
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(public_json.encode()) > 4096 or len(items_json.encode()) > 65_536:
            raise BusinessDataError("l2_manifest_too_large")
        with self._sessions.begin() as session:
            case = session.scalar(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.case_id == manifest.case_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            )
            if case is None:
                raise BusinessDataError("l2_case_not_accessible")
            existing = session.scalar(
                select(L2ContextManifestRow).where(
                    L2ContextManifestRow.case_id == manifest.case_id,
                    L2ContextManifestRow.step_id == manifest.step_id,
                )
            )
            if existing is not None:
                if (
                    existing.manifest_id != manifest.manifest_id
                    or existing.pack_hash != manifest.pack_hash
                    or existing.scope_fingerprint != manifest.scope_fingerprint
                ):
                    raise BusinessDataError("l2_manifest_conflict")
                return self._manifest_record(existing)
            row = L2ContextManifestRow(
                manifest_id=manifest.manifest_id,
                case_id=manifest.case_id,
                step_id=manifest.step_id,
                schema_version=manifest.schema_version,
                context_policy_version=manifest.context_policy_version,
                scope_fingerprint=manifest.scope_fingerprint,
                pack_hash=manifest.pack_hash,
                essential_complete=manifest.essential_complete,
                candidate_count=manifest.candidate_count,
                selected_count=manifest.selected_count,
                duplicate_count=manifest.duplicate_count,
                irrelevant_count=manifest.irrelevant_count,
                stale_count=manifest.stale_count,
                conflict_count=manifest.conflict_count,
                out_of_scope_count=manifest.out_of_scope_count,
                truncated_count=manifest.truncated_count,
                refresh_count=manifest.refresh_count,
                candidate_estimated_tokens=manifest.candidate_estimated_tokens,
                selected_estimated_tokens=manifest.selected_estimated_tokens,
                pack_estimated_input_tokens=manifest.pack_estimated_input_tokens,
                input_budget_tokens=manifest.input_budget_tokens,
                reduction_basis_points=manifest.reduction_basis_points,
                truncated=manifest.truncated,
                failure_attribution=manifest.failure_attribution,
                public_summary_json=public_json,
                diagnostic_items_json=items_json,
                context_preparation_ms=manifest.context_preparation_ms,
                created_at=manifest.created_at,
            )
            session.add(row)
            session.flush()
            return self._manifest_record(row)

    def list_manifests(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
    ) -> tuple[L2ContextManifest, ...]:
        """按创建时间读取本人 Case 的脱敏 Manifest，供本地诊断和指标。"""

        with self._sessions() as session:
            allowed = session.scalar(
                select(L2SupportCaseRow.case_id).where(
                    L2SupportCaseRow.case_id == case_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            )
            if allowed is None:
                return ()
            rows = session.scalars(
                select(L2ContextManifestRow)
                .where(L2ContextManifestRow.case_id == case_id)
                .order_by(L2ContextManifestRow.created_at, L2ContextManifestRow.step_id)
            ).all()
            try:
                return tuple(self._manifest_record(row) for row in rows)
            except (ValueError, json.JSONDecodeError):
                return ()

    def get_case_metrics(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
    ) -> L2CaseMetrics | None:
        """从持久事实聚合公开 Case 指标，不执行 Graph 或业务工具。"""

        with self._sessions() as session:
            case = session.scalar(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.case_id == case_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            )
            if case is None:
                return None
            events = session.scalars(
                select(L2CaseEventRow).where(L2CaseEventRow.case_id == case_id)
            ).all()
            manifests = session.scalars(
                select(L2ContextManifestRow).where(
                    L2ContextManifestRow.case_id == case_id
                )
            ).all()
            calls = session.scalars(
                select(LlmCallEventRow).where(LlmCallEventRow.case_id == case_id)
            ).all()
            duration = max(
                0,
                int(
                    (
                        _as_utc(case.updated_at) - _as_utc(case.created_at)
                    ).total_seconds()
                    * 1000
                ),
            )
            case_record = self._case_record(case)
            return L2CaseMetrics(
                steps=case.steps_used,
                model_calls=len(calls),
                tool_calls=sum(event.tool_category is not None for event in events),
                user_questions=sum("user" in event.event_type for event in events),
                approvals=sum("approval" in event.event_type for event in events),
                candidate_count=sum(item.candidate_count for item in manifests),
                selected_count=sum(item.selected_count for item in manifests),
                duplicate_count=sum(item.duplicate_count for item in manifests),
                stale_count=sum(item.stale_count for item in manifests),
                conflict_count=sum(item.conflict_count for item in manifests),
                truncated_count=sum(item.truncated_count for item in manifests),
                candidate_estimated_tokens=sum(
                    item.candidate_estimated_tokens for item in manifests
                ),
                selected_estimated_tokens=sum(
                    item.selected_estimated_tokens for item in manifests
                ),
                provider_input_tokens=sum(item.input_tokens for item in calls),
                provider_output_tokens=sum(item.output_tokens for item in calls),
                usage_sources=tuple(
                    dict.fromkeys(
                        cast(L2UsageSource, item.usage_source) for item in calls
                    )
                ),
                context_duration_ms=sum(
                    item.context_preparation_ms for item in manifests
                ),
                model_duration_ms=sum(item.duration_ms for item in calls),
                tool_duration_ms=sum(
                    event.duration_ms
                    for event in events
                    if event.tool_category is not None
                ),
                case_duration_ms=duration,
                budget_limits=case_record.budget,
                budget_used=case_record.usage,
                status=cast(L2CaseStatus, case.status),
                stop_reason=cast(L2StopReason | None, case.stop_reason),
                failure_attribution=cast(
                    L2FailureAttribution | None, case.failure_attribution
                ),
            )

    def count_manifests(self) -> int:
        """返回 Context Manifest 数量，供回放零副作用测试使用。"""

        with self._sessions() as session:
            return int(
                session.scalar(select(func.count()).select_from(L2ContextManifestRow))
                or 0
            )

    def _case_record_for_read(
        self,
        session: Session,
        row: L2SupportCaseRow,
    ) -> L2CaseRecord:
        """读取时校验 v0.7 Manifest 完整性，损坏时只降级 Trace 状态。"""

        record = self._case_record(row)
        if record.trace_state != "complete":
            return record
        manifests = session.scalars(
            select(L2ContextManifestRow).where(
                L2ContextManifestRow.case_id == row.case_id
            )
        ).all()
        calls = session.scalars(
            select(LlmCallEventRow).where(LlmCallEventRow.case_id == row.case_id)
        ).all()
        try:
            validated_ids = {
                self._manifest_record(manifest).manifest_id for manifest in manifests
            }
        except (ValueError, json.JSONDecodeError):
            return record.model_copy(update={"trace_state": "unavailable"})
        if any(
            call.manifest_id is None or call.manifest_id not in validated_ids
            for call in calls
        ):
            return record.model_copy(update={"trace_state": "unavailable"})
        return record

    @staticmethod
    def _case_record(row: L2SupportCaseRow) -> L2CaseRecord:
        """把 ORM Case 行转换为不可变领域记录。"""

        return L2CaseRecord(
            case_id=row.case_id,
            thread_id=row.thread_id,
            subject_id=row.subject_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            related_order_id=row.related_order_id,
            issue_summary=row.issue_summary,
            status=cast(L2CaseStatus, row.status),
            stop_reason=cast(L2StopReason | None, row.stop_reason),
            model_name=row.model_name,
            prompt_version=row.prompt_version,
            toolset_version=row.toolset_version,
            context_policy_version=row.context_policy_version,
            trace_state=cast(L2TraceState, row.trace_state),
            failure_attribution=cast(
                L2FailureAttribution | None, row.failure_attribution
            ),
            budget=L2BudgetLimits(
                max_steps=row.max_steps,
                max_model_calls=row.max_model_calls,
                max_tool_calls=row.max_tool_calls,
                max_estimated_tokens=row.max_estimated_tokens,
                max_active_milliseconds=row.max_active_milliseconds,
                max_invocation_milliseconds=row.max_invocation_milliseconds,
                max_consecutive_tool_failures=(row.max_consecutive_tool_failures),
            ),
            usage=L2BudgetState(
                steps_used=row.steps_used,
                model_calls_used=row.model_calls_used,
                tool_calls_used=row.tool_calls_used,
                estimated_tokens_used=row.estimated_tokens_used,
                active_milliseconds=row.active_milliseconds,
                consecutive_tool_failures=row.consecutive_tool_failures,
                last_action_signature=row.last_action_signature,
                repeated_action_count=row.repeated_action_count,
            ),
            final_response=row.final_response,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            completed_at=(
                _as_utc(row.completed_at) if row.completed_at is not None else None
            ),
        )

    @staticmethod
    def _event_record(row: L2CaseEventRow) -> L2PublicTraceEvent:
        """把 ORM 事件行转换为经过 Schema 校验的公开事件。"""

        return L2PublicTraceEvent(
            event_id=row.event_id,
            case_id=row.case_id,
            event_key=row.event_key,
            sequence_no=row.sequence_no,
            payload_version=row.payload_version,
            step_number=row.step_number,
            event_type=row.event_type,
            tool_category=cast(L2ToolName | None, row.tool_category),
            risk=cast("str | None", row.risk),
            parameter_summary=(
                json.loads(row.parameter_summary_json)
                if row.parameter_summary_json is not None
                else None
            ),
            result_code=row.result_code,
            evidence_ids=tuple(json.loads(row.evidence_refs_json)),
            duration_ms=row.duration_ms,
            context_summary=(
                L2ContextPublicSummary.model_validate_json(row.context_summary_json)
                if row.context_summary_json is not None
                else None
            ),
            created_at=_as_utc(row.created_at),
        )

    @staticmethod
    def _model_call_record(row: LlmCallEventRow) -> L2ModelCallRecord:
        """把 ORM 模型调用行转换为不可变领域记录。"""

        return L2ModelCallRecord(
            call_id=row.call_id,
            user_id=row.user_id,
            thread_id=row.thread_id,
            case_id=row.case_id,
            step_id=row.step_id,
            model_name=row.model_name,
            manifest_id=row.manifest_id,
            status=cast("str", row.status),
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            charged_tokens=row.charged_tokens,
            duration_ms=row.duration_ms,
            usage_source=cast(L2UsageSource, row.usage_source),
            created_at=_as_utc(row.created_at),
            completed_at=(
                _as_utc(row.completed_at) if row.completed_at is not None else None
            ),
        )

    @staticmethod
    def _manifest_record(row: L2ContextManifestRow) -> L2ContextManifest:
        """把 ORM Manifest 行重新校验为不含正文的领域模型。"""

        return L2ContextManifest(
            manifest_id=row.manifest_id,
            schema_version=row.schema_version,
            case_id=row.case_id,
            step_id=row.step_id,
            context_policy_version=row.context_policy_version,
            scope_fingerprint=row.scope_fingerprint,
            pack_hash=row.pack_hash,
            essential_complete=row.essential_complete,
            candidate_count=row.candidate_count,
            selected_count=row.selected_count,
            duplicate_count=row.duplicate_count,
            irrelevant_count=row.irrelevant_count,
            stale_count=row.stale_count,
            conflict_count=row.conflict_count,
            out_of_scope_count=row.out_of_scope_count,
            truncated_count=row.truncated_count,
            refresh_count=row.refresh_count,
            candidate_estimated_tokens=row.candidate_estimated_tokens,
            selected_estimated_tokens=row.selected_estimated_tokens,
            pack_estimated_input_tokens=row.pack_estimated_input_tokens,
            input_budget_tokens=row.input_budget_tokens,
            reduction_basis_points=row.reduction_basis_points,
            truncated=row.truncated,
            failure_attribution=cast(
                L2FailureAttribution | None, row.failure_attribution
            ),
            public_summary=L2ContextPublicSummary.model_validate_json(
                row.public_summary_json
            ),
            items=tuple(
                L2ContextManifestItem.model_validate(item)
                for item in json.loads(row.diagnostic_items_json)
            ),
            context_preparation_ms=row.context_preparation_ms,
            created_at=_as_utc(row.created_at),
        )
