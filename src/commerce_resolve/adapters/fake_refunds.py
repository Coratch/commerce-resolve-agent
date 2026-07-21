"""提供离线 Graph 测试和 Eval 使用的可注入 Fake Refund Gateway。"""

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from commerce_resolve.access import BusinessScope
from commerce_resolve.business_models import MockRefundRecord, RefundActionRecord
from commerce_resolve.models import (
    RefundContext,
    RefundExecutionResult,
    RefundPreview,
    RefundVerification,
    ToolResult,
)


class FakeRefundGateway:
    """在内存中模拟退款上下文、审批动作、结果和有限失败模式。"""

    def __init__(
        self,
        contexts: Mapping[tuple[str, str, str], RefundContext],
        *,
        execution_mode: str = "success",
    ) -> None:
        """复制业务上下文并设置一次固定执行模式。"""

        self._contexts = dict(contexts)
        self._actions: dict[str, RefundActionRecord] = {}
        self._refunds: dict[str, MockRefundRecord] = {}
        self.execution_mode = execution_mode
        self.reserve_calls = 0
        self.execute_calls = 0
        self.verify_calls = 0

    def action_count(self) -> int:
        """返回内存动作数量，供 Eval 断言预览幂等性。"""

        return len(self._actions)

    def refund_count(self) -> int:
        """返回内存退款数量，供 Eval 断言资金副作用上限。"""

        return len(self._refunds)

    def _key(self, scope: BusinessScope, order_id: str) -> tuple[str, str, str]:
        """构造包含用户与工作区的隔离查询键。"""

        return scope.user_id, scope.workspace_id, order_id

    def get_refund_context(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[RefundContext]:
        """按完整可信作用域返回当前 Fake 业务事实。"""

        context = self._contexts.get(self._key(scope, order_id))
        if context is None or scope.access_mode != "registered":
            return ToolResult[RefundContext](
                outcome="unavailable",
                error_code="order_unavailable",
            )
        return ToolResult[RefundContext](outcome="found", value=context)

    def list_refunds(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[tuple[MockRefundRecord, ...]]:
        """按完整 Fake 作用域列出订单对应的退款结果。"""

        if self._contexts.get(self._key(scope, order_id)) is None:
            return ToolResult[tuple[MockRefundRecord, ...]](
                outcome="unavailable",
                error_code="order_unavailable",
            )
        refunds = tuple(
            refund
            for action_id, refund in self._refunds.items()
            if (
                (action := self._actions.get(action_id)) is not None
                and action.order_id == order_id
                and action.user_id == scope.user_id
                and action.workspace_id == scope.workspace_id
            )
        )
        return ToolResult[tuple[MockRefundRecord, ...]](
            outcome="found",
            value=refunds,
        )

    def replace_context(self, scope: BusinessScope, context: RefundContext) -> None:
        """替换指定订单事实，供过期预览测试模拟并发业务变化。"""

        self._contexts[self._key(scope, context.order_id)] = context

    def reserve_preview(
        self,
        scope: BusinessScope,
        preview: RefundPreview,
    ) -> RefundActionRecord:
        """按 action_id 与 preview_hash 幂等保存待审批动作。"""

        self.reserve_calls += 1
        existing = self._actions.get(preview.action_id)
        if existing is not None:
            if existing.preview_hash != preview.preview_hash:
                raise ValueError("refund_action_conflict")
            return existing
        active = next(
            (
                action
                for action in self._actions.values()
                if action.order_id == preview.order_id
                and action.status
                in {"awaiting_approval", "executing", "unknown", "completed"}
            ),
            None,
        )
        if active is not None:
            raise ValueError("refund_conflict")
        context = self._contexts.get(self._key(scope, preview.order_id))
        if context is None or context.payment_id is None:
            raise ValueError("refund_payment_missing")
        now = datetime.now(UTC)
        action = RefundActionRecord(
            action_id=preview.action_id,
            task_id=preview.task_id,
            subject_id=scope.user_id,
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            order_id=preview.order_id,
            payment_id=context.payment_id,
            reason_code=preview.reason.code,
            reason_detail=preview.reason.detail,
            amount_minor=preview.amount_minor,
            currency=preview.currency,
            channel=preview.channel,
            policy_version=preview.policy_version,
            policy_fact_ids=preview.policy_fact_ids,
            facts_fingerprint=preview.facts_fingerprint,
            preview_hash=preview.preview_hash,
            idempotency_key=hashlib.sha256(
                f"refund:{preview.action_id}".encode()
            ).hexdigest(),
            status="awaiting_approval",
            created_at=now,
            updated_at=now,
        )
        self._actions[action.action_id] = action
        return action

    def get_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
    ) -> RefundActionRecord | None:
        """读取同时匹配任务、用户和工作区的 Fake 动作。"""

        action = self._actions.get(action_id)
        if (
            action is None
            or action.task_id != task_id
            or action.user_id != scope.user_id
            or action.workspace_id != scope.workspace_id
        ):
            return None
        return action

    def get_refund_by_action(
        self,
        scope: BusinessScope,
        action_id: str,
    ) -> MockRefundRecord | None:
        """读取当前作用域动作对应的 Fake 退款结果。"""

        action = self._actions.get(action_id)
        if (
            action is None
            or action.user_id != scope.user_id
            or action.workspace_id != scope.workspace_id
        ):
            return None
        return self._refunds.get(action_id)

    def reject_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """幂等拒绝匹配预览的 Fake 动作。"""

        return self._close_action(
            scope,
            task_id,
            action_id,
            preview_hash,
            target_status="rejected",
        )

    def mark_stale(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """幂等标记事实已变化的 Fake 动作。"""

        return self._close_action(
            scope,
            task_id,
            action_id,
            preview_hash,
            target_status="stale",
        )

    def _close_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
        *,
        target_status: str,
    ) -> RefundActionRecord:
        """校验绑定后更新内存动作终态。"""

        action = self.get_action(scope, task_id, action_id)
        if action is None:
            raise ValueError("refund_action_not_accessible")
        if action.preview_hash != preview_hash:
            raise ValueError("refund_preview_stale")
        if action.status == target_status:
            return action
        if action.status != "awaiting_approval":
            raise ValueError("refund_action_closed")
        updated = action.model_copy(
            update={
                "status": target_status,
                "decided_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        self._actions[action_id] = updated
        return updated

    def execute_refund(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        expected_fingerprint: str,
    ) -> RefundExecutionResult:
        """按配置模拟成功、拒绝、写前失败、未知或验证不一致。"""

        self.execute_calls += 1
        action = self.get_action(scope, task_id, action_id)
        if action is None:
            raise ValueError("refund_action_not_accessible")
        existing = self._refunds.get(action_id)
        if existing is not None and action.status == "completed":
            return RefundExecutionResult(
                outcome="succeeded",
                action_id=action_id,
                refund_id=existing.refund_id,
                result_code="idempotent_replay",
            )
        if action.facts_fingerprint != expected_fingerprint:
            return RefundExecutionResult(
                outcome="business_rejected",
                action_id=action_id,
                result_code="refund_preview_stale",
            )
        if self.execution_mode == "business_rejected":
            return RefundExecutionResult(
                outcome="business_rejected",
                action_id=action_id,
                result_code="gateway_business_rejected",
            )
        if self.execution_mode == "fail_before_write":
            return RefundExecutionResult(
                outcome="failed_before_write",
                action_id=action_id,
                result_code="gateway_failed_before_write",
            )
        now = datetime.now(UTC)
        status = (
            "unknown" if self.execution_mode == "unknown_after_write" else "succeeded"
        )
        refund = MockRefundRecord(
            refund_id=f"MOCK-RFD-{action_id}",
            action_id=action_id,
            order_id=action.order_id,
            amount_minor=action.amount_minor,
            currency=action.currency,
            channel=action.channel,
            status=status,
            gateway_result_code=self.execution_mode,
            created_at=now,
            updated_at=now,
        )
        self._refunds[action_id] = refund
        self._actions[action_id] = action.model_copy(
            update={
                "status": "unknown" if status == "unknown" else "completed",
                "decided_at": now,
                "updated_at": now,
            }
        )
        if status == "unknown":
            return RefundExecutionResult(
                outcome="result_unknown",
                action_id=action_id,
                refund_id=refund.refund_id,
                result_code="gateway_result_unknown",
            )
        return RefundExecutionResult(
            outcome=(
                "verification_mismatch"
                if self.execution_mode == "verification_mismatch"
                else "succeeded"
            ),
            action_id=action_id,
            refund_id=refund.refund_id,
            result_code=self.execution_mode,
        )

    def verify_refund(
        self,
        scope: BusinessScope,
        action_id: str,
    ) -> RefundVerification:
        """根据内存退款和失败模式返回独立回读验证结果。"""

        self.verify_calls += 1
        refund = self.get_refund_by_action(scope, action_id)
        if refund is None:
            return RefundVerification(
                verified=False,
                action_id=action_id,
                result_code="refund_not_found",
            )
        verified = bool(
            refund.status == "succeeded"
            and self.execution_mode != "verification_mismatch"
        )
        return RefundVerification(
            verified=verified,
            action_id=action_id,
            refund_id=refund.refund_id,
            amount_minor=refund.amount_minor,
            status=refund.status,
            result_code="verified" if verified else "verification_mismatch",
        )
