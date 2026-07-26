"""从 v1.0 权威业务表构建 v1.1 售后中心只读投影。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Engine, exists, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_resolve.business_models import (
    OrderStatus,
    PaymentChannel,
    PaymentCurrency,
    PaymentStatus,
    ProductCategory,
    RefundStatus,
    ShipmentStatus,
    format_minor_units,
)
from commerce_resolve.service_center import (
    catalog_image_url,
    customer_order_stage,
    fulfillment_summary,
    map_l2_status,
    map_refund_status,
    shipment_milestones,
)
from commerce_resolve.service_center_models import (
    PublicServiceStep,
    ServiceRecordDetail,
    ServiceRecordSummary,
    ServiceStatus,
    SupportAmountSummary,
    SupportCitation,
    SupportOrderDetail,
    SupportOrderItem,
    SupportOrderSummary,
    SupportOverview,
    SupportPayment,
    SupportProductPreview,
    SupportRefund,
    SupportShipment,
    SupportShipmentPackage,
    SupportShipmentPackageItem,
)

from .sqlalchemy_models import (
    L2SupportCaseRow,
    MockPaymentRow,
    MockRefundRow,
    OrderItemRow,
    OrderRow,
    RefundActionRow,
    ShipmentPackageItemRow,
    ShipmentPackageRow,
    ShipmentRow,
)


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间解释为 UTC。"""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _escape_like(value: str) -> str:
    """转义 SQLite LIKE 通配符，使搜索词按普通文本匹配。"""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snapshot_state(item: OrderItemRow) -> str:
    """根据下单快照字段完整度返回兼容展示状态。"""

    values = (
        item.product_ref,
        item.variant_title,
        item.unit_amount_minor,
        item.currency,
        item.image_ref,
        item.catalog_version,
    )
    present = sum(value is not None for value in values)
    if present == 0:
        return "legacy"
    return "complete" if present == len(values) else "partial"


class SqliteSupportCenterReader:
    """按完整注册用户作用域读取订单并投影退款与 L2 服务。"""

    def __init__(self, engine: Engine) -> None:
        """保存共享 Engine；每次查询使用独立只读 Session。"""

        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def overview(
        self,
        *,
        subject_id: str,
        user_id: str,
        workspace_id: str,
    ) -> SupportOverview:
        """返回最近五笔订单和五条活动服务，并标记是否还有更多。"""

        orders = self.list_orders(
            user_id=user_id,
            workspace_id=workspace_id,
            limit=6,
        )
        services = self.list_services(
            subject_id=subject_id,
            user_id=user_id,
            workspace_id=workspace_id,
            view="active",
            limit=6,
        )
        return SupportOverview(
            active_services=tuple(services[:5]),
            recent_orders=tuple(orders[:5]),
            has_more_orders=len(orders) > 5,
            has_more_services=len(services) > 5,
        )

    def list_orders(
        self,
        *,
        user_id: str,
        workspace_id: str,
        limit: int = 20,
        before: tuple[datetime, str] | None = None,
        q: str | None = None,
        view: str = "all",
    ) -> list[SupportOrderSummary]:
        """在服务端按查询和客户视图过滤，再稳定分页订单摘要。"""

        bounded = max(1, min(limit, 51))
        if view not in {"all", "processing", "shipping", "delivered", "after_sales"}:
            raise ValueError("unsupported order view")
        normalized_query = q.strip().casefold() if q is not None else ""
        with self._sessions() as session:
            statement = select(OrderRow).where(
                OrderRow.user_id == user_id,
                OrderRow.workspace_id == workspace_id,
            )
            if normalized_query:
                pattern = f"%{_escape_like(normalized_query)}%"
                matching_item = exists(
                    select(OrderItemRow.id).where(
                        OrderItemRow.order_pk == OrderRow.id,
                        or_(
                            func.lower(OrderItemRow.title).like(pattern, escape="\\"),
                            func.lower(OrderItemRow.sku).like(pattern, escape="\\"),
                        ),
                    )
                )
                statement = statement.where(
                    or_(
                        func.lower(OrderRow.order_id).like(pattern, escape="\\"),
                        matching_item,
                    )
                )
            if view == "processing":
                statement = statement.where(OrderRow.status == "processing")
            elif view == "shipping":
                statement = statement.where(OrderRow.status == "shipped")
            elif view == "delivered":
                statement = statement.where(OrderRow.status == "delivered")
            elif view == "after_sales":
                statement = statement.where(
                    or_(
                        exists(
                            select(RefundActionRow.action_id).where(
                                RefundActionRow.order_pk == OrderRow.id
                            )
                        ),
                        exists(
                            select(L2SupportCaseRow.case_id).where(
                                L2SupportCaseRow.user_id == OrderRow.user_id,
                                L2SupportCaseRow.workspace_id == OrderRow.workspace_id,
                                L2SupportCaseRow.related_order_id == OrderRow.order_id,
                            )
                        ),
                    )
                )
            if before is not None:
                before_time, before_id = before
                statement = statement.where(
                    or_(
                        OrderRow.updated_at < before_time,
                        (
                            (OrderRow.updated_at == before_time)
                            & (OrderRow.order_id < before_id)
                        ),
                    )
                )
            rows = session.scalars(
                statement.order_by(
                    OrderRow.updated_at.desc(), OrderRow.order_id.desc()
                ).limit(bounded)
            ).all()
            summaries: list[SupportOrderSummary] = []
            for row in rows:
                items = session.scalars(
                    select(OrderItemRow).where(OrderItemRow.order_pk == row.id)
                ).all()
                summary = self._order_summary(session, row, item_rows=items)
                summaries.append(summary)
            return summaries

    def get_order(
        self,
        *,
        user_id: str,
        workspace_id: str,
        order_id: str,
    ) -> SupportOrderDetail | None:
        """读取当前用户订单详情；不存在与越权统一返回空。"""

        with self._sessions() as session:
            row = session.scalar(
                select(OrderRow).where(
                    OrderRow.user_id == user_id,
                    OrderRow.workspace_id == workspace_id,
                    OrderRow.order_id == order_id.upper(),
                )
            )
            return self._order_detail(session, row) if row is not None else None

    def list_services(
        self,
        *,
        subject_id: str,
        user_id: str,
        workspace_id: str,
        view: str,
        limit: int = 20,
        before: tuple[datetime, str] | None = None,
    ) -> list[ServiceRecordSummary]:
        """合并退款与 L2 事实，按客户状态筛选并稳定倒序分页。"""

        if view not in {"active", "history"}:
            raise ValueError("unsupported service view")
        with self._sessions() as session:
            refunds = session.scalars(
                select(RefundActionRow).where(
                    RefundActionRow.subject_id == subject_id,
                    RefundActionRow.user_id == user_id,
                    RefundActionRow.workspace_id == workspace_id,
                )
            ).all()
            l2_cases = session.scalars(
                select(L2SupportCaseRow).where(
                    L2SupportCaseRow.subject_id == subject_id,
                    L2SupportCaseRow.user_id == user_id,
                    L2SupportCaseRow.workspace_id == workspace_id,
                )
            ).all()
            records = [self._refund_summary(session, row) for row in refunds] + [
                self._l2_summary(session, row) for row in l2_cases
            ]
        active = {"waiting_user", "in_progress", "needs_attention"}
        records = [
            item for item in records if (item.status in active) == (view == "active")
        ]
        if before is not None:
            before_time, before_id = before
            records = [
                item
                for item in records
                if item.updated_at < before_time
                or (item.updated_at == before_time and item.service_id < before_id)
            ]
        records.sort(key=lambda item: (item.updated_at, item.service_id), reverse=True)
        return records[: max(1, min(limit, 51))]

    def get_service(
        self,
        *,
        service_id: str,
        subject_id: str,
        user_id: str,
        workspace_id: str,
    ) -> ServiceRecordDetail | None:
        """按资源前缀和完整身份作用域读取单条服务详情。"""

        prefix, separator, raw_id = service_id.partition(":")
        if not separator or not raw_id:
            return None
        with self._sessions() as session:
            if prefix == "refund":
                row = session.scalar(
                    select(RefundActionRow).where(
                        RefundActionRow.action_id == raw_id,
                        RefundActionRow.subject_id == subject_id,
                        RefundActionRow.user_id == user_id,
                        RefundActionRow.workspace_id == workspace_id,
                    )
                )
                return self._refund_detail(session, row) if row is not None else None
            if prefix == "l2":
                row = session.scalar(
                    select(L2SupportCaseRow).where(
                        L2SupportCaseRow.case_id == raw_id,
                        L2SupportCaseRow.subject_id == subject_id,
                        L2SupportCaseRow.user_id == user_id,
                        L2SupportCaseRow.workspace_id == workspace_id,
                    )
                )
                return self._l2_detail(session, row) if row is not None else None
        return None

    def _order_summary(
        self,
        session: Session,
        row: OrderRow,
        *,
        item_rows: list[OrderItemRow] | None = None,
    ) -> SupportOrderSummary:
        """把订单关联事实转换为列表摘要。"""

        items = (
            item_rows
            or session.scalars(
                select(OrderItemRow)
                .where(OrderItemRow.order_pk == row.id)
                .order_by(OrderItemRow.sku)
            ).all()
        )
        shipment = session.scalar(
            select(ShipmentRow).where(ShipmentRow.order_pk == row.id)
        )
        packages = session.scalars(
            select(ShipmentPackageRow)
            .where(ShipmentPackageRow.order_pk == row.id)
            .order_by(ShipmentPackageRow.package_id)
        ).all()
        payment = session.scalar(
            select(MockPaymentRow).where(MockPaymentRow.order_pk == row.id)
        )
        service_statuses: list[tuple[datetime, ServiceStatus, str]] = []
        refund_action = session.scalar(
            select(RefundActionRow)
            .where(RefundActionRow.order_pk == row.id)
            .order_by(RefundActionRow.updated_at.desc())
            .limit(1)
        )
        if refund_action is not None:
            service_statuses.append(
                (
                    _as_utc(refund_action.updated_at),
                    map_refund_status(refund_action.status),
                    self._refund_next_action(refund_action.status)
                    or "查看退款处理结果",
                )
            )
        l2_case = session.scalar(
            select(L2SupportCaseRow)
            .where(
                L2SupportCaseRow.user_id == row.user_id,
                L2SupportCaseRow.workspace_id == row.workspace_id,
                L2SupportCaseRow.related_order_id == row.order_id,
            )
            .order_by(L2SupportCaseRow.updated_at.desc())
            .limit(1)
        )
        if l2_case is not None:
            service_statuses.append(
                (
                    _as_utc(l2_case.updated_at),
                    map_l2_status(l2_case.status),
                    self._l2_next_action(l2_case.status) or "查看复杂售后处理结果",
                )
            )
        latest_service = (
            max(service_statuses, key=lambda item: item[0])
            if service_statuses
            else None
        )
        return SupportOrderSummary(
            order_id=row.order_id,
            status=cast(OrderStatus, row.status),
            item_count=len(items),
            item_title_preview=items[0].title if items else None,
            preview_items=tuple(
                SupportProductPreview(
                    sku=item.sku,
                    title=item.title,
                    variant_title=item.variant_title,
                    quantity=item.quantity,
                    image_url=catalog_image_url(item.image_ref),
                    image_alt=item.title,
                )
                for item in items[:2]
            ),
            shipment_status=(
                cast(ShipmentStatus, shipment.status) if shipment else None
            ),
            fulfillment_summary=fulfillment_summary(
                tuple(cast(ShipmentStatus, item.status) for item in packages),
                cast(ShipmentStatus, shipment.status) if shipment else None,
            ),
            customer_stage=customer_order_stage(cast(OrderStatus, row.status)),
            estimated_delivery_at=(
                shipment.estimated_delivery_at if shipment else None
            ),
            payment_amount=(
                format_minor_units(payment.amount_minor)
                if payment is not None
                else None
            ),
            latest_service_status=(
                latest_service[1] if latest_service is not None else None
            ),
            latest_service_summary=(
                latest_service[2] if latest_service is not None else None
            ),
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def _order_detail(self, session: Session, row: OrderRow) -> SupportOrderDetail:
        """把订单、物流、支付和退款转换为客户详情。"""

        item_rows = session.scalars(
            select(OrderItemRow)
            .where(OrderItemRow.order_pk == row.id)
            .order_by(OrderItemRow.sku)
        ).all()
        shipment_row = session.scalar(
            select(ShipmentRow).where(ShipmentRow.order_pk == row.id)
        )
        payment_row = session.scalar(
            select(MockPaymentRow).where(MockPaymentRow.order_pk == row.id)
        )
        refund_rows = session.scalars(
            select(MockRefundRow)
            .where(MockRefundRow.order_pk == row.id)
            .order_by(MockRefundRow.created_at.desc())
        ).all()
        package_rows = session.scalars(
            select(ShipmentPackageRow)
            .where(ShipmentPackageRow.order_pk == row.id)
            .order_by(ShipmentPackageRow.package_id)
        ).all()
        shipment = (
            SupportShipment(
                status=cast(ShipmentStatus, shipment_row.status),
                last_event=shipment_row.last_event,
                estimated_delivery_at=shipment_row.estimated_delivery_at,
                updated_at=_as_utc(shipment_row.updated_at),
            )
            if shipment_row is not None
            else None
        )
        payment = (
            SupportPayment(
                amount=format_minor_units(payment_row.amount_minor),
                currency=cast(PaymentCurrency, payment_row.currency),
                channel=cast(PaymentChannel, payment_row.channel),
                status=cast(PaymentStatus, payment_row.status),
            )
            if payment_row is not None
            else None
        )
        blocking_action = session.scalar(
            select(RefundActionRow.action_id).where(
                RefundActionRow.order_pk == row.id,
                RefundActionRow.status.in_(
                    ("awaiting_approval", "executing", "unknown", "completed")
                ),
            )
        )
        actions = ["ask_assistant", "view_policy"]
        if (
            payment_row is not None
            and payment_row.status == "settled"
            and blocking_action is None
        ):
            actions.append("request_refund")
        item_by_pk = {item.id: item for item in item_rows}
        packages: list[SupportShipmentPackage] = []
        for package in package_rows:
            links = session.scalars(
                select(ShipmentPackageItemRow)
                .where(ShipmentPackageItemRow.package_pk == package.id)
                .order_by(ShipmentPackageItemRow.order_item_pk)
            ).all()
            packages.append(
                SupportShipmentPackage(
                    package_id=package.package_id,
                    carrier=package.carrier,
                    tracking_number=package.tracking_number,
                    status=cast(ShipmentStatus, package.status),
                    last_event=package.last_event,
                    estimated_delivery_at=package.estimated_delivery_at,
                    items=tuple(
                        SupportShipmentPackageItem(
                            sku=item_by_pk[link.order_item_pk].sku,
                            title=item_by_pk[link.order_item_pk].title,
                            quantity=link.quantity,
                        )
                        for link in links
                        if link.order_item_pk in item_by_pk
                    ),
                    updated_at=_as_utc(package.updated_at),
                )
            )
        item_subtotal = (
            sum(
                item.unit_amount_minor * item.quantity
                for item in item_rows
                if item.unit_amount_minor is not None
            )
            if item_rows
            and all(item.unit_amount_minor is not None for item in item_rows)
            else None
        )
        refunded_amount = sum(item.amount_minor for item in refund_rows)
        return SupportOrderDetail(
            summary=self._order_summary(session, row),
            items=tuple(
                SupportOrderItem(
                    sku=item.sku,
                    title=item.title,
                    quantity=item.quantity,
                    product_category=cast(ProductCategory, item.product_category),
                    product_ref=item.product_ref,
                    variant_title=item.variant_title,
                    unit_amount=(
                        format_minor_units(item.unit_amount_minor)
                        if item.unit_amount_minor is not None
                        else None
                    ),
                    currency=cast(PaymentCurrency | None, item.currency),
                    image_url=catalog_image_url(item.image_ref),
                    image_alt=item.title,
                    snapshot_state=cast(str, _snapshot_state(item)),
                )
                for item in item_rows
            ),
            shipment=shipment,
            packages=tuple(packages),
            shipment_milestones=shipment_milestones(shipment),
            payment=payment,
            refunds=tuple(
                SupportRefund(
                    refund_id=item.refund_id,
                    amount=format_minor_units(item.amount_minor),
                    currency=cast(PaymentCurrency, item.currency),
                    status=cast(RefundStatus, item.status),
                    updated_at=_as_utc(item.updated_at),
                )
                for item in refund_rows
            ),
            amount_summary=SupportAmountSummary(
                item_subtotal=(
                    format_minor_units(item_subtotal)
                    if item_subtotal is not None
                    else None
                ),
                paid_amount=(
                    format_minor_units(payment_row.amount_minor)
                    if payment_row is not None
                    else None
                ),
                refunded_amount=(
                    format_minor_units(refunded_amount) if refunded_amount > 0 else None
                ),
            ),
            next_step=self._next_order_step(
                cast(OrderStatus, row.status),
                tuple(actions),
            ),
            available_actions=tuple(actions),
        )

    @staticmethod
    def _next_order_step(
        status: OrderStatus,
        actions: tuple[str, ...],
    ) -> str:
        """根据权威状态和允许动作给出一个可执行的客户下一步。"""

        if "request_refund" in actions and status == "delivered":
            return "如商品存在问题，可先咨询政策或发起 Mock 退款。"
        return {
            "processing": "等待商家发货；如需了解规则，可咨询售后助手。",
            "shipped": "关注包裹进度；遇到异常可让助手联合核对物流与政策。",
            "delivered": "核对商品状态，并在需要时查看售后政策。",
            "cancelled": "订单已关闭，可查看支付或退款记录。",
        }[status]

    def _refund_summary(
        self,
        session: Session,
        row: RefundActionRow,
    ) -> ServiceRecordSummary:
        """把退款动作转换为客户服务摘要。"""

        order_id = session.scalar(
            select(OrderRow.order_id).where(OrderRow.id == row.order_pk)
        )
        status = map_refund_status(row.status)
        return ServiceRecordSummary(
            service_id=f"refund:{row.action_id}",
            kind="refund",
            status=status,
            order_id=str(order_id) if order_id is not None else None,
            thread_id=row.task_id,
            title="Mock 退款申请",
            next_action=self._refund_next_action(row.status),
            product_preview=self._product_preview(session, row.order_pk),
            updated_at=_as_utc(row.updated_at),
        )

    def _refund_detail(
        self,
        session: Session,
        row: RefundActionRow,
    ) -> ServiceRecordDetail:
        """把退款动作和最终结果转换为公开详情。"""

        summary = self._refund_summary(session, row)
        approval_state = (
            "current"
            if row.status == "awaiting_approval"
            else "upcoming"
            if row.status == "stale"
            else "completed"
        )
        execution_state = (
            "completed"
            if row.status == "completed"
            else "current"
            if row.status in {"executing", "failed", "unknown", "verification_failed"}
            else "upcoming"
        )
        refund = session.scalar(
            select(MockRefundRow).where(MockRefundRow.action_id == row.action_id)
        )
        result_summary = None
        if row.status == "completed" and refund is not None:
            result_summary = (
                f"Mock 退款 {format_minor_units(refund.amount_minor)} "
                f"{refund.currency} 已完成并回读验证。"
            )
        elif row.status == "rejected":
            result_summary = "本次退款申请已取消。"
        elif row.status in {"failed", "unknown", "verification_failed"}:
            result_summary = "本次退款尚未得到可验证的成功结果。"
        fact_ids = tuple(json.loads(row.policy_fact_ids_json))
        return ServiceRecordDetail(
            summary=summary,
            public_steps=(
                PublicServiceStep(
                    key="submitted",
                    title="已提交退款申请",
                    state="completed",
                    occurred_at=_as_utc(row.created_at),
                ),
                PublicServiceStep(
                    key="approval",
                    title="确认退款决定",
                    state=cast(str, approval_state),
                    occurred_at=(
                        _as_utc(row.decided_at) if row.decided_at is not None else None
                    ),
                ),
                PublicServiceStep(
                    key="execution",
                    title="执行并验证 Mock 退款",
                    state=cast(str, execution_state),
                    occurred_at=(
                        _as_utc(refund.updated_at) if refund is not None else None
                    ),
                ),
            ),
            result_summary=result_summary,
            citations=tuple(
                SupportCitation(
                    source="售后政策",
                    version=row.policy_version,
                    locator=fact_id,
                )
                for fact_id in fact_ids
                if isinstance(fact_id, str)
            ),
        )

    def _l2_summary(
        self,
        session: Session,
        row: L2SupportCaseRow,
    ) -> ServiceRecordSummary:
        """把 L2 Case 转换为客户服务摘要。"""

        status = map_l2_status(row.status)
        order_pk = (
            session.scalar(
                select(OrderRow.id).where(
                    OrderRow.user_id == row.user_id,
                    OrderRow.workspace_id == row.workspace_id,
                    OrderRow.order_id == row.related_order_id,
                )
            )
            if row.related_order_id is not None
            else None
        )
        return ServiceRecordSummary(
            service_id=f"l2:{row.case_id}",
            kind="l2_support",
            status=status,
            order_id=row.related_order_id,
            thread_id=row.thread_id,
            title=row.issue_summary,
            next_action=self._l2_next_action(row.status),
            product_preview=(
                self._product_preview(session, order_pk)
                if order_pk is not None
                else None
            ),
            updated_at=_as_utc(row.updated_at),
        )

    def _l2_detail(
        self,
        session: Session,
        row: L2SupportCaseRow,
    ) -> ServiceRecordDetail:
        """把 L2 Case 的公开状态和最终回复转换为服务详情。"""

        summary = self._l2_summary(session, row)
        terminal = row.status in {
            "l2_resolved",
            "l2_unresolved",
            "l2_budget_exhausted",
            "l2_cancelled",
            "l2_stopped",
        }
        return ServiceRecordDetail(
            summary=summary,
            public_steps=(
                PublicServiceStep(
                    key="submitted",
                    title="已提交 AI 深度处理",
                    state="completed",
                    occurred_at=_as_utc(row.created_at),
                ),
                PublicServiceStep(
                    key="processing",
                    title="AI 深度处理",
                    state="completed" if terminal else "current",
                    occurred_at=None,
                ),
                PublicServiceStep(
                    key="result",
                    title="形成可验证结果",
                    state="completed" if terminal else "upcoming",
                    occurred_at=_as_utc(row.completed_at) if row.completed_at else None,
                ),
            ),
            result_summary=row.final_response,
            citations=(),
        )

    @staticmethod
    def _product_preview(
        session: Session,
        order_pk: str,
    ) -> SupportProductPreview | None:
        """读取订单第一条快照作为服务记录的有限商品预览。"""

        item = session.scalar(
            select(OrderItemRow)
            .where(OrderItemRow.order_pk == order_pk)
            .order_by(OrderItemRow.sku)
            .limit(1)
        )
        if item is None:
            return None
        return SupportProductPreview(
            sku=item.sku,
            title=item.title,
            variant_title=item.variant_title,
            quantity=item.quantity,
            image_url=catalog_image_url(item.image_ref),
            image_alt=item.title,
        )

    @staticmethod
    def _refund_next_action(status: str) -> str | None:
        """把退款动作状态转换为统一客户下一步。"""

        return {
            "awaiting_approval": "请确认或拒绝退款申请",
            "stale": "退款预览已失效，请重新申请",
            "failed": "退款未完成，请返回会话继续处理",
            "unknown": "退款结果待核实，请返回会话查看",
            "verification_failed": "退款结果验证失败，请返回会话处理",
        }.get(status)

    @staticmethod
    def _l2_next_action(status: str) -> str | None:
        """把 AI 二线状态转换为统一客户下一步。"""

        return {
            "l2_waiting_user": "请返回会话补充所需信息",
            "l2_waiting_approval": "请返回会话确认待处理动作",
            "l2_unresolved": "当前问题尚未解决，可重新描述后继续",
            "l2_budget_exhausted": "本次处理已达到安全上限",
            "l2_stopped": "本次处理已安全停止",
        }.get(status)
