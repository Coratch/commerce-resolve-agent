"""提供游客目录和售后中心的确定性客户投影规则。"""

from typing import cast

from commerce_resolve.business_models import (
    OrderStatus,
    ProductCategory,
    ShipmentRecord,
    ShipmentStatus,
    format_minor_units,
)
from commerce_resolve.demo_catalog import DemoCatalogService
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.service_center_models import (
    MilestoneState,
    ServiceStatus,
    SupportAmountSummary,
    SupportOrderDetail,
    SupportOrderItem,
    SupportOrderSummary,
    SupportOverview,
    SupportProductPreview,
    SupportShipment,
    SupportShipmentMilestone,
    SupportShipmentPackage,
    SupportShipmentPackageItem,
)

GUEST_ORDER_ID = "ORD-001"


def catalog_image_url(image_ref: str | None) -> str | None:
    """把受控目录资源标识转换为同源 URL，拒绝其他路径。"""

    if image_ref is None or not image_ref.startswith("catalog/v1.3/"):
        return None
    if ".." in image_ref.split("/"):
        return None
    return f"/{image_ref}"


def customer_order_stage(status: OrderStatus) -> str:
    """把内部订单状态转换为客户任务阶段。"""

    return {
        "processing": "等待发货",
        "shipped": "关注收货",
        "delivered": "可申请售后",
        "cancelled": "订单已关闭",
    }[status]


def fulfillment_summary(
    package_statuses: tuple[ShipmentStatus, ...],
    shipment_status: ShipmentStatus | None,
) -> str | None:
    """根据包裹事实生成稳定履约摘要，不补造缺失包裹。"""

    if not package_statuses:
        return (
            {
                "preparing": "正在备货",
                "in_transit": "运输中",
                "delivered": "已签收",
            }[shipment_status]
            if shipment_status is not None
            else None
        )
    delivered = sum(status == "delivered" for status in package_statuses)
    if delivered == len(package_statuses):
        return f"{delivered}/{len(package_statuses)} 个包裹已送达"
    if delivered:
        return f"{delivered}/{len(package_statuses)} 个包裹已送达"
    if any(status == "in_transit" for status in package_statuses):
        return f"{len(package_statuses)} 个包裹运输中"
    return f"{len(package_statuses)} 个包裹正在准备"


def shipment_milestones(
    shipment: ShipmentRecord | SupportShipment | None,
) -> tuple[SupportShipmentMilestone, ...]:
    """依据当前物流状态生成三步进度，不补造历史时间。"""

    if shipment is None:
        return ()
    keys = ("preparing", "in_transit", "delivered")
    titles = {"preparing": "等待揽收", "in_transit": "运输中", "delivered": "已签收"}
    current_index = keys.index(shipment.status)
    result: list[SupportShipmentMilestone] = []
    for index, key in enumerate(keys):
        state: MilestoneState = (
            "completed"
            if index < current_index
            else "current"
            if index == current_index
            else "upcoming"
        )
        result.append(
            SupportShipmentMilestone(
                key=cast(ShipmentStatus, key),
                title=titles[key],
                state=state,
                detail=shipment.last_event if index == current_index else None,
                occurred_at=shipment.updated_at if index == current_index else None,
            )
        )
    return tuple(result)


class GuestSupportCatalog:
    """集中提供游客 Graph 与客户页面共用的只读演示事实。"""

    def __init__(self, catalog: DemoCatalogService | None = None) -> None:
        """从版本化目录构造游客演示订单，避免维护第二套商品事实。"""

        service = catalog or DemoCatalogService()
        self._record = service.build_order_record(
            scenario_id="single-package-shipping",
            user_id="guest-demo",
            workspace_id="demo",
        )

    def order_view(self, actor_id: str) -> OrderView:
        """为游客 Graph 返回绑定当前匿名主体的订单视图。"""

        return OrderView(
            order_id=self._record.order_id,
            user_id=actor_id,
            status=self._record.status,
        )

    def shipment_view(self) -> ShipmentView:
        """为游客 Graph 返回固定演示物流视图。"""

        shipment = self._record.shipment
        if shipment is None:
            raise RuntimeError("游客演示订单缺少物流事实")
        return ShipmentView(
            order_id=self._record.order_id,
            status=shipment.status,
            last_event=shipment.last_event,
            estimated_delivery_at=shipment.estimated_delivery_at,
        )

    def overview(self) -> SupportOverview:
        """返回游客售后首页，不创建服务记录或会话。"""

        return SupportOverview(
            active_services=(),
            recent_orders=(self.order_summary(),),
            has_more_orders=False,
            has_more_services=False,
        )

    def order_summary(self) -> SupportOrderSummary:
        """把固定演示订单转换为客户列表摘要。"""

        shipment = self._record.shipment
        return SupportOrderSummary(
            order_id=self._record.order_id,
            status=cast(OrderStatus, self._record.status),
            item_count=len(self._record.items),
            item_title_preview=self._record.items[0].title,
            preview_items=tuple(
                SupportProductPreview(
                    sku=item.sku,
                    title=item.title,
                    variant_title=item.variant_title,
                    quantity=item.quantity,
                    image_url=catalog_image_url(item.image_ref),
                    image_alt=item.title,
                )
                for item in self._record.items[:2]
            ),
            shipment_status=shipment.status if shipment else None,
            fulfillment_summary=fulfillment_summary(
                tuple(item.status for item in self._record.packages),
                shipment.status if shipment else None,
            ),
            customer_stage=customer_order_stage(self._record.status),
            estimated_delivery_at=(
                shipment.estimated_delivery_at if shipment else None
            ),
            created_at=self._record.created_at,
            updated_at=self._record.updated_at,
        )

    def order_detail(self, order_id: str) -> SupportOrderDetail | None:
        """仅在订单号匹配时返回游客详情，避免暴露其他数据。"""

        if order_id.upper() != self._record.order_id:
            return None
        shipment = self._record.shipment
        public_shipment = (
            SupportShipment(
                status=shipment.status,
                last_event=shipment.last_event,
                estimated_delivery_at=shipment.estimated_delivery_at,
                updated_at=shipment.updated_at,
            )
            if shipment is not None
            else None
        )
        return SupportOrderDetail(
            summary=self.order_summary(),
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
                    currency=item.currency,
                    image_url=catalog_image_url(item.image_ref),
                    image_alt=item.title,
                )
                for item in self._record.items
            ),
            shipment=public_shipment,
            packages=tuple(
                SupportShipmentPackage(
                    package_id=package.package_id,
                    carrier=package.carrier,
                    tracking_number=package.tracking_number,
                    status=package.status,
                    last_event=package.last_event,
                    estimated_delivery_at=package.estimated_delivery_at,
                    items=tuple(
                        SupportShipmentPackageItem(
                            sku=item.sku,
                            title=next(
                                order_item.title
                                for order_item in self._record.items
                                if order_item.sku == item.sku
                            ),
                            quantity=item.quantity,
                        )
                        for item in package.items
                    ),
                    updated_at=package.updated_at,
                )
                for package in self._record.packages
            ),
            shipment_milestones=shipment_milestones(public_shipment),
            payment=None,
            refunds=(),
            amount_summary=SupportAmountSummary(
                item_subtotal=format_minor_units(
                    sum(
                        (item.unit_amount_minor or 0) * item.quantity
                        for item in self._record.items
                    )
                ),
            ),
            next_step=("关注包裹进度；遇到异常可让助手联合核对物流与政策。"),
            available_actions=("ask_assistant", "view_policy"),
        )


def map_refund_status(status: str) -> ServiceStatus:
    """把退款动作状态映射为稳定客户服务状态。"""

    mapping: dict[str, ServiceStatus] = {
        "awaiting_approval": "waiting_user",
        "executing": "in_progress",
        "completed": "completed",
        "rejected": "cancelled",
        "stale": "needs_attention",
        "failed": "needs_attention",
        "unknown": "needs_attention",
        "verification_failed": "needs_attention",
    }
    try:
        return mapping[status]
    except KeyError as error:
        raise ValueError("unknown refund service status") from error


def map_l2_status(status: str) -> ServiceStatus:
    """把 L2 Case 状态映射为稳定客户服务状态。"""

    mapping: dict[str, ServiceStatus] = {
        "l2_active": "in_progress",
        "l2_waiting_user": "waiting_user",
        "l2_waiting_approval": "waiting_user",
        "l2_resolved": "completed",
        "l2_cancelled": "cancelled",
        "l2_unresolved": "needs_attention",
        "l2_budget_exhausted": "needs_attention",
        "l2_stopped": "needs_attention",
    }
    try:
        return mapping[status]
    except KeyError as error:
        raise ValueError("unknown l2 service status") from error
