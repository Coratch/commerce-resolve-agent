"""实现 Mock 支付、退款动作与退款结果的 SQLite 事务仓库。"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.access import BusinessScope
from commerce_resolve.business_models import (
    MockPaymentInput,
    MockPaymentRecord,
    MockRefundRecord,
    PaymentChannel,
    PaymentCurrency,
    PaymentStatus,
    RefundActionRecord,
    RefundStatus,
    amount_to_minor_units,
)
from commerce_resolve.models import (
    RefundContext,
    RefundExecutionResult,
    RefundPreview,
    RefundVerification,
    ToolResult,
)
from commerce_resolve.refund_rules import build_facts_fingerprint

from .sqlalchemy_models import (
    MockPaymentRow,
    MockRefundRow,
    OrderRow,
    RefundActionRow,
    RefundAuditEventRow,
    ShipmentRow,
    WorkspaceRow,
    utc_now,
)
from .sqlite_business import BusinessDataError


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqliteRefundRepository:
    """在业务数据库内提供受作用域保护的 Mock 支付与退款事务。"""

    def __init__(
        self,
        engine: Engine,
        *,
        now_provider: Callable[[], datetime] = utc_now,
    ) -> None:
        """保存共享 Engine 和可替换时钟，每个方法独立创建短事务。"""

        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._now = now_provider

    def _now_utc(self) -> datetime:
        """返回规范化后的当前 UTC 时间。"""

        return _as_utc(self._now())

    def upsert_payment(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
        data: MockPaymentInput,
    ) -> MockPaymentRecord:
        """创建或更新退款前 Mock 支付，并使受影响的待审批预览失效。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            order = self._require_order(session, user_id, workspace_id, order_id)
            blocking_action = session.scalar(
                select(RefundActionRow.action_id).where(
                    RefundActionRow.order_pk == order.id,
                    RefundActionRow.status.in_(("executing", "completed", "unknown")),
                )
            )
            blocking_refund = session.scalar(
                select(MockRefundRow.refund_id).where(
                    MockRefundRow.order_pk == order.id,
                    MockRefundRow.status.in_(("processing", "succeeded", "unknown")),
                )
            )
            if blocking_action is not None or blocking_refund is not None:
                raise BusinessDataError("payment_locked")

            row = session.scalar(
                select(MockPaymentRow).where(MockPaymentRow.order_pk == order.id)
            )
            if row is None:
                row = MockPaymentRow(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    order_pk=order.id,
                    amount_minor=amount_to_minor_units(data.amount),
                    currency=data.currency,
                    channel=data.channel,
                    status=data.status,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.amount_minor = amount_to_minor_units(data.amount)
                row.currency = data.currency
                row.channel = data.channel
                row.status = data.status
                row.updated_at = now
            self._invalidate_pending_actions(
                session,
                order_pk=order.id,
                actor_id=user_id,
                result_code="payment_changed",
                now=now,
            )
            session.flush()
            return self._to_payment(row, order.order_id)

    def get_payment(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> MockPaymentRecord | None:
        """按用户和工作区读取订单的 Mock 支付，不泄露越权订单。"""

        with self._sessions() as session:
            order = self._require_order(session, user_id, workspace_id, order_id)
            row = session.scalar(
                select(MockPaymentRow).where(MockPaymentRow.order_pk == order.id)
            )
            return self._to_payment(row, order.order_id) if row is not None else None

    def list_refunds(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> tuple[MockRefundRecord, ...]:
        """按完整私有作用域列出指定订单的 Mock 退款结果。"""

        with self._sessions() as session:
            order = self._require_order(session, user_id, workspace_id, order_id)
            rows = session.scalars(
                select(MockRefundRow)
                .where(MockRefundRow.order_pk == order.id)
                .order_by(MockRefundRow.created_at.desc())
            ).all()
            return tuple(self._to_refund(row, order.order_id) for row in rows)

    def get_refund_context(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> RefundContext:
        """按私有作用域读取资格判断所需的最新订单、物流、支付和退款事实。"""

        with self._sessions() as session:
            order = self._require_order(session, user_id, workspace_id, order_id)
            return self._context_from_order(session, order)

    def reserve_preview(
        self,
        *,
        user_id: str,
        workspace_id: str,
        preview: RefundPreview,
    ) -> RefundActionRecord:
        """幂等保存服务端预览，并用数据库约束阻止同订单冲突动作。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            order = self._require_order(
                session,
                user_id,
                workspace_id,
                preview.order_id,
            )
            payment = session.scalar(
                select(MockPaymentRow).where(MockPaymentRow.order_pk == order.id)
            )
            if payment is None:
                raise BusinessDataError("refund_payment_missing")
            existing = session.get(RefundActionRow, preview.action_id)
            if existing is not None:
                if existing.preview_hash != preview.preview_hash:
                    raise BusinessDataError("refund_action_conflict")
                return self._to_action(existing, order.order_id)
            active = session.scalar(
                select(RefundActionRow).where(
                    RefundActionRow.order_pk == order.id,
                    RefundActionRow.status.in_(
                        ("awaiting_approval", "executing", "unknown", "completed")
                    ),
                )
            )
            if active is not None:
                if (
                    active.status == "awaiting_approval"
                    and active.facts_fingerprint != preview.facts_fingerprint
                ):
                    active.status = "stale"
                    active.updated_at = now
                    self._add_audit(
                        session,
                        active,
                        event_key="stale:new_preview",
                        event_type="stale",
                        actor_id=user_id,
                        result_code="facts_changed",
                        now=now,
                    )
                else:
                    raise BusinessDataError("refund_conflict")
            row = RefundActionRow(
                action_id=preview.action_id,
                task_id=preview.task_id,
                subject_id=user_id,
                user_id=user_id,
                workspace_id=workspace_id,
                order_pk=order.id,
                payment_id=payment.id,
                reason_code=preview.reason.code,
                reason_detail=preview.reason.detail,
                amount_minor=preview.amount_minor,
                currency=preview.currency,
                channel=preview.channel,
                policy_version=preview.policy_version,
                policy_fact_ids_json=json.dumps(list(preview.policy_fact_ids)),
                facts_fingerprint=preview.facts_fingerprint,
                preview_hash=preview.preview_hash,
                idempotency_key=hashlib.sha256(
                    f"refund:{preview.action_id}".encode()
                ).hexdigest(),
                status="awaiting_approval",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            self._add_audit(
                session,
                row,
                event_key="preview:reserved",
                event_type="preview_reserved",
                actor_id=user_id,
                result_code="awaiting_approval",
                now=now,
            )
            try:
                session.flush()
            except IntegrityError as error:
                raise BusinessDataError("refund_conflict") from error
            return self._to_action(row, order.order_id)

    def get_action(
        self,
        *,
        user_id: str,
        workspace_id: str,
        task_id: str,
        action_id: str,
    ) -> RefundActionRecord | None:
        """读取同时匹配用户、工作区、任务和动作的退款审批记录。"""

        with self._sessions() as session:
            row = session.scalar(
                select(RefundActionRow).where(
                    RefundActionRow.action_id == action_id,
                    RefundActionRow.task_id == task_id,
                    RefundActionRow.user_id == user_id,
                    RefundActionRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                return None
            order_id = session.scalar(
                select(OrderRow.order_id).where(OrderRow.id == row.order_pk)
            )
            return self._to_action(row, str(order_id)) if order_id is not None else None

    def get_refund_by_action(
        self,
        *,
        user_id: str,
        workspace_id: str,
        action_id: str,
    ) -> MockRefundRecord | None:
        """按动作和私有作用域读取一笔 Mock 退款。"""

        with self._sessions() as session:
            row = session.scalar(
                select(MockRefundRow).where(
                    MockRefundRow.action_id == action_id,
                    MockRefundRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                return None
            order = session.scalar(
                select(OrderRow).where(
                    OrderRow.id == row.order_pk,
                    OrderRow.user_id == user_id,
                )
            )
            return self._to_refund(row, order.order_id) if order is not None else None

    def reject_action(
        self,
        *,
        user_id: str,
        workspace_id: str,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """幂等拒绝待审批动作，不创建任何 Mock 退款。"""

        return self._close_without_execution(
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            action_id=action_id,
            preview_hash=preview_hash,
            target_status="rejected",
            result_code="user_rejected",
        )

    def mark_stale(
        self,
        *,
        user_id: str,
        workspace_id: str,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """幂等关闭已因业务或政策变化失效的预览。"""

        return self._close_without_execution(
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            action_id=action_id,
            preview_hash=preview_hash,
            target_status="stale",
            result_code="preview_stale",
        )

    def execute_refund(
        self,
        *,
        user_id: str,
        workspace_id: str,
        task_id: str,
        action_id: str,
        expected_fingerprint: str,
    ) -> RefundExecutionResult:
        """在单事务内校验指纹、幂等写入退款并把支付标记为 refunded。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            action = session.scalar(
                select(RefundActionRow).where(
                    RefundActionRow.action_id == action_id,
                    RefundActionRow.task_id == task_id,
                    RefundActionRow.user_id == user_id,
                    RefundActionRow.workspace_id == workspace_id,
                )
            )
            if action is None:
                raise BusinessDataError("refund_action_not_accessible")
            existing = session.scalar(
                select(MockRefundRow).where(MockRefundRow.action_id == action_id)
            )
            if existing is not None and action.status == "completed":
                return RefundExecutionResult(
                    outcome="succeeded",
                    action_id=action_id,
                    refund_id=existing.refund_id,
                    result_code="idempotent_replay",
                )
            if action.status != "awaiting_approval":
                raise BusinessDataError(
                    "refund_preview_stale"
                    if action.status == "stale"
                    else "refund_action_closed"
                )
            order = session.get(OrderRow, action.order_pk)
            payment = session.get(MockPaymentRow, action.payment_id)
            if order is None or payment is None:
                raise BusinessDataError("refund_business_facts_missing")
            current = self._context_from_order(session, order)
            current_fingerprint = build_facts_fingerprint(
                current,
                policy_version=action.policy_version,
                policy_fact_ids=tuple(json.loads(action.policy_fact_ids_json)),
            )
            if (
                action.facts_fingerprint != expected_fingerprint
                or current_fingerprint != expected_fingerprint
            ):
                action.status = "stale"
                action.updated_at = now
                self._add_audit(
                    session,
                    action,
                    event_key="execution:stale",
                    event_type="stale",
                    actor_id=user_id,
                    result_code="facts_changed",
                    now=now,
                )
                return RefundExecutionResult(
                    outcome="business_rejected",
                    action_id=action_id,
                    result_code="refund_preview_stale",
                )
            action.status = "executing"
            action.decided_at = now
            action.updated_at = now
            self._add_audit(
                session,
                action,
                event_key="approval:approved",
                event_type="approved",
                actor_id=user_id,
                result_code="approved",
                now=now,
            )
            refund_id = f"MOCK-RFD-{action_id}"
            refund = MockRefundRow(
                refund_id=refund_id,
                action_id=action.action_id,
                payment_id=payment.id,
                workspace_id=workspace_id,
                order_pk=order.id,
                idempotency_key=action.idempotency_key,
                amount_minor=action.amount_minor,
                currency=action.currency,
                channel=action.channel,
                status="succeeded",
                gateway_result_code="mock_succeeded",
                created_at=now,
                updated_at=now,
            )
            session.add(refund)
            payment.status = "refunded"
            payment.updated_at = now
            action.status = "completed"
            action.updated_at = now
            self._add_audit(
                session,
                action,
                event_key="execution:succeeded",
                event_type="executed",
                actor_id=user_id,
                result_code="mock_succeeded",
                now=now,
            )
            session.flush()
            return RefundExecutionResult(
                outcome="succeeded",
                action_id=action_id,
                refund_id=refund_id,
                result_code="mock_succeeded",
            )

    def verify_refund(
        self,
        *,
        user_id: str,
        workspace_id: str,
        action_id: str,
    ) -> RefundVerification:
        """回读动作、退款与支付，并幂等记录最终验证结果。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            action = session.scalar(
                select(RefundActionRow).where(
                    RefundActionRow.action_id == action_id,
                    RefundActionRow.user_id == user_id,
                    RefundActionRow.workspace_id == workspace_id,
                )
            )
            if action is None:
                return RefundVerification(
                    verified=False,
                    action_id=action_id,
                    result_code="refund_action_not_accessible",
                )
            refund = session.scalar(
                select(MockRefundRow).where(MockRefundRow.action_id == action_id)
            )
            payment = session.get(MockPaymentRow, action.payment_id)
            if refund is None:
                self._add_audit(
                    session,
                    action,
                    event_key="verification:refund_not_found",
                    event_type="verification_failed",
                    actor_id=user_id,
                    result_code="refund_not_found",
                    now=now,
                )
                return RefundVerification(
                    verified=False,
                    action_id=action_id,
                    result_code="refund_not_found",
                )
            verified = bool(
                action.status == "completed"
                and refund.status == "succeeded"
                and payment is not None
                and payment.status == "refunded"
                and refund.amount_minor == action.amount_minor
                and refund.currency == action.currency
                and refund.channel == action.channel
            )
            result_code = "verified" if verified else "verification_mismatch"
            self._add_audit(
                session,
                action,
                event_key=f"verification:{result_code}",
                event_type="verified" if verified else "verification_failed",
                actor_id=user_id,
                result_code=result_code,
                now=now,
            )
            return RefundVerification(
                verified=verified,
                action_id=action_id,
                refund_id=refund.refund_id,
                amount_minor=refund.amount_minor,
                status=cast(RefundStatus, refund.status),
                result_code=result_code,
            )

    def count_refunds(self) -> int:
        """返回 Mock 退款总数，供幂等测试和 Eval 断言副作用。"""

        with self._sessions() as session:
            return int(
                session.scalar(select(func.count()).select_from(MockRefundRow)) or 0
            )

    def count_audit_events(self) -> int:
        """返回退款审计事件总数，供固定 Eval 验证。"""

        with self._sessions() as session:
            return int(
                session.scalar(select(func.count()).select_from(RefundAuditEventRow))
                or 0
            )

    def _require_order(
        self,
        session: Session,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> OrderRow:
        """验证工作区所有者并返回同作用域订单。"""

        owner = session.scalar(
            select(WorkspaceRow.owner_user_id).where(
                WorkspaceRow.id == workspace_id,
                WorkspaceRow.owner_user_id == user_id,
            )
        )
        if owner is None:
            raise BusinessDataError("order_not_accessible")
        order = session.scalar(
            select(OrderRow).where(
                OrderRow.workspace_id == workspace_id,
                OrderRow.user_id == user_id,
                OrderRow.order_id == order_id.upper(),
            )
        )
        if order is None:
            raise BusinessDataError("order_not_accessible")
        return order

    def _context_from_order(
        self,
        session: Session,
        order: OrderRow,
    ) -> RefundContext:
        """从同一数据库会话构造确定性退款上下文。"""

        shipment = session.scalar(
            select(ShipmentRow).where(ShipmentRow.order_pk == order.id)
        )
        payment = session.scalar(
            select(MockPaymentRow).where(MockPaymentRow.order_pk == order.id)
        )
        rows = session.scalars(
            select(MockRefundRow).where(
                MockRefundRow.order_pk == order.id,
                MockRefundRow.status.in_(("processing", "succeeded", "unknown")),
            )
        ).all()
        return RefundContext(
            order_id=order.order_id,
            order_status=cast(object, order.status),
            shipment_status=(cast(object, shipment.status) if shipment else None),
            shipment_last_event=shipment.last_event if shipment else None,
            payment_id=payment.id if payment else None,
            paid_amount_minor=payment.amount_minor if payment else 0,
            currency=(cast(PaymentCurrency, payment.currency) if payment else None),
            channel=(cast(PaymentChannel, payment.channel) if payment else None),
            payment_status=(cast(PaymentStatus, payment.status) if payment else None),
            active_or_completed_refund_amount_minor=sum(
                row.amount_minor for row in rows
            ),
            has_conflicting_refund=bool(rows),
        )

    def _close_without_execution(
        self,
        *,
        user_id: str,
        workspace_id: str,
        task_id: str,
        action_id: str,
        preview_hash: str,
        target_status: str,
        result_code: str,
    ) -> RefundActionRecord:
        """在单事务中幂等关闭待审批动作并记录决定。"""

        now = self._now_utc()
        with self._sessions.begin() as session:
            action = session.scalar(
                select(RefundActionRow).where(
                    RefundActionRow.action_id == action_id,
                    RefundActionRow.task_id == task_id,
                    RefundActionRow.user_id == user_id,
                    RefundActionRow.workspace_id == workspace_id,
                )
            )
            if action is None:
                raise BusinessDataError("refund_action_not_accessible")
            if action.preview_hash != preview_hash:
                raise BusinessDataError("refund_preview_stale")
            if action.status == target_status:
                order_id = session.scalar(
                    select(OrderRow.order_id).where(OrderRow.id == action.order_pk)
                )
                return self._to_action(action, str(order_id))
            if action.status != "awaiting_approval":
                raise BusinessDataError(
                    "refund_preview_stale"
                    if action.status == "stale"
                    else "refund_action_closed"
                )
            action.status = target_status
            action.decided_at = now
            action.updated_at = now
            self._add_audit(
                session,
                action,
                event_key=f"decision:{target_status}",
                event_type=target_status,
                actor_id=user_id,
                result_code=result_code,
                now=now,
            )
            order_id = session.scalar(
                select(OrderRow.order_id).where(OrderRow.id == action.order_pk)
            )
            return self._to_action(action, str(order_id))

    def _add_audit(
        self,
        session: Session,
        action: RefundActionRow,
        *,
        event_key: str,
        event_type: str,
        actor_id: str,
        result_code: str,
        now: datetime,
    ) -> None:
        """按 action 与稳定 event_key 追加一次脱敏审计事件。"""

        with session.no_autoflush:
            exists = session.scalar(
                select(RefundAuditEventRow.id).where(
                    RefundAuditEventRow.action_id == action.action_id,
                    RefundAuditEventRow.event_key == event_key,
                )
            )
        if exists is None:
            session.add(
                RefundAuditEventRow(
                    id=str(uuid4()),
                    action_id=action.action_id,
                    event_key=event_key,
                    event_type=event_type,
                    actor_id=actor_id,
                    result_code=result_code,
                    preview_hash=action.preview_hash,
                    created_at=now,
                )
            )

    def _invalidate_pending_actions(
        self,
        session: Session,
        *,
        order_pk: str,
        actor_id: str,
        result_code: str,
        now: datetime,
    ) -> None:
        """把业务事实变化前生成的待审批动作标记为 stale 并追加一次审计。"""

        actions = session.scalars(
            select(RefundActionRow).where(
                RefundActionRow.order_pk == order_pk,
                RefundActionRow.status == "awaiting_approval",
            )
        ).all()
        for action in actions:
            action.status = "stale"
            action.updated_at = now
            event_key = f"stale:{result_code}"
            exists = session.scalar(
                select(RefundAuditEventRow.id).where(
                    RefundAuditEventRow.action_id == action.action_id,
                    RefundAuditEventRow.event_key == event_key,
                )
            )
            if exists is None:
                session.add(
                    RefundAuditEventRow(
                        id=str(uuid4()),
                        action_id=action.action_id,
                        event_key=event_key,
                        event_type="stale",
                        actor_id=actor_id,
                        result_code=result_code,
                        preview_hash=action.preview_hash,
                        created_at=now,
                    )
                )

    def _to_payment(
        self,
        row: MockPaymentRow,
        order_id: str,
    ) -> MockPaymentRecord:
        """把 Mock 支付 ORM 行转换为不可变领域记录。"""

        return MockPaymentRecord(
            payment_id=row.id,
            order_id=order_id,
            amount_minor=row.amount_minor,
            currency=cast(PaymentCurrency, row.currency),
            channel=cast(PaymentChannel, row.channel),
            status=cast(PaymentStatus, row.status),
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def _to_refund(
        self,
        row: MockRefundRow,
        order_id: str,
    ) -> MockRefundRecord:
        """把 Mock 退款 ORM 行转换为不含内部作用域字段的领域记录。"""

        return MockRefundRecord(
            refund_id=row.refund_id,
            action_id=row.action_id,
            order_id=order_id,
            amount_minor=row.amount_minor,
            currency=cast(PaymentCurrency, row.currency),
            channel=cast(PaymentChannel, row.channel),
            status=cast(RefundStatus, row.status),
            gateway_result_code=row.gateway_result_code,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def _to_action(
        self,
        row: RefundActionRow,
        order_id: str,
    ) -> RefundActionRecord:
        """把退款动作 ORM 行转换为不可变领域记录。"""

        return RefundActionRecord(
            action_id=row.action_id,
            task_id=row.task_id,
            subject_id=row.subject_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            order_id=order_id,
            payment_id=row.payment_id,
            reason_code=cast(object, row.reason_code),
            reason_detail=row.reason_detail,
            amount_minor=row.amount_minor,
            currency=cast(PaymentCurrency, row.currency),
            channel=cast(PaymentChannel, row.channel),
            policy_version=row.policy_version,
            policy_fact_ids=tuple(json.loads(row.policy_fact_ids_json)),
            facts_fingerprint=row.facts_fingerprint,
            preview_hash=row.preview_hash,
            idempotency_key=row.idempotency_key,
            status=cast(object, row.status),
            created_at=_as_utc(row.created_at),
            decided_at=_as_utc(row.decided_at) if row.decided_at else None,
            updated_at=_as_utc(row.updated_at),
        )


class SqliteRefundGateway:
    """把可信 BusinessScope 转换为 SQLite 退款仓储调用。"""

    def __init__(self, repository: SqliteRefundRepository) -> None:
        """保存不持有额外连接状态的退款仓储。"""

        self._repository = repository

    def _registered(self, scope: BusinessScope) -> None:
        """拒绝游客或 CLI 作用域触发 v0.4 私有退款能力。"""

        if scope.access_mode != "registered":
            raise BusinessDataError("refund_not_authorized")

    def get_refund_context(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[RefundContext]:
        """按可信作用域读取退款上下文，并统一不可访问结果。"""

        self._registered(scope)
        try:
            context = self._repository.get_refund_context(
                user_id=scope.user_id,
                workspace_id=scope.workspace_id,
                order_id=order_id,
            )
        except BusinessDataError:
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
        """按可信作用域列出订单 Mock 退款，并统一不可访问结果。"""

        self._registered(scope)
        try:
            refunds = self._repository.list_refunds(
                user_id=scope.user_id,
                workspace_id=scope.workspace_id,
                order_id=order_id,
            )
        except BusinessDataError:
            return ToolResult[tuple[MockRefundRecord, ...]](
                outcome="unavailable",
                error_code="order_unavailable",
            )
        return ToolResult[tuple[MockRefundRecord, ...]](
            outcome="found",
            value=refunds,
        )

    def reserve_preview(
        self,
        scope: BusinessScope,
        preview: RefundPreview,
    ) -> RefundActionRecord:
        """保存当前注册用户的待审批预览。"""

        self._registered(scope)
        return self._repository.reserve_preview(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            preview=preview,
        )

    def get_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
    ) -> RefundActionRecord | None:
        """读取当前注册用户任务中的动作。"""

        self._registered(scope)
        return self._repository.get_action(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            task_id=task_id,
            action_id=action_id,
        )

    def get_refund_by_action(
        self,
        scope: BusinessScope,
        action_id: str,
    ) -> MockRefundRecord | None:
        """读取当前注册用户动作对应的 Mock 退款。"""

        self._registered(scope)
        return self._repository.get_refund_by_action(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            action_id=action_id,
        )

    def reject_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """拒绝当前任务的待审批动作。"""

        self._registered(scope)
        return self._repository.reject_action(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            task_id=task_id,
            action_id=action_id,
            preview_hash=preview_hash,
        )

    def mark_stale(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """关闭已过期的退款预览。"""

        self._registered(scope)
        return self._repository.mark_stale(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            task_id=task_id,
            action_id=action_id,
            preview_hash=preview_hash,
        )

    def execute_refund(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        expected_fingerprint: str,
    ) -> RefundExecutionResult:
        """幂等执行当前注册用户已批准的 Mock 退款。"""

        self._registered(scope)
        return self._repository.execute_refund(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            task_id=task_id,
            action_id=action_id,
            expected_fingerprint=expected_fingerprint,
        )

    def verify_refund(
        self,
        scope: BusinessScope,
        action_id: str,
    ) -> RefundVerification:
        """回读并验证当前注册用户的 Mock 退款。"""

        self._registered(scope)
        return self._repository.verify_refund(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            action_id=action_id,
        )
