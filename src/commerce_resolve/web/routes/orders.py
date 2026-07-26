"""提供注册用户私有演示订单与物流的确定性 CRUD API。"""

from fastapi import APIRouter, Request

from commerce_resolve.adapters.sqlite_business import BusinessDataError
from commerce_resolve.business_models import (
    MockPaymentInput,
    OrderCreate,
    OrderRecord,
    OrderUpdate,
)

from ..dependencies import (
    get_services,
    require_registered_access,
)
from ..errors import api_error
from ..schemas import (
    DeleteResponse,
    OrdersResponse,
    PublicOrder,
    PublicOrderItem,
    PublicPayment,
    PublicRefund,
    PublicShipment,
    RefundsResponse,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _public_order(
    record: OrderRecord,
    *,
    payment: PublicPayment | None = None,
    refunds: tuple[PublicRefund, ...] = (),
) -> PublicOrder:
    """移除内部作用域字段后构造含 Mock 交易摘要的订单响应。"""

    shipment = (
        PublicShipment(
            status=record.shipment.status,
            last_event=record.shipment.last_event,
            estimated_delivery_at=record.shipment.estimated_delivery_at,
        )
        if record.shipment is not None
        else None
    )
    return PublicOrder(
        order_id=record.order_id,
        status=record.status,
        items=tuple(PublicOrderItem.from_record(item) for item in record.items),
        shipment=shipment,
        payment=payment,
        refunds=refunds,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _public_order_with_transactions(
    request: Request,
    *,
    user_id: str,
    workspace_id: str,
    record: OrderRecord,
) -> PublicOrder:
    """读取同一私有作用域的 Mock 支付与退款并合并为公开订单。"""

    repository = get_services(request).require_refund_repository()
    payment = repository.get_payment(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id=record.order_id,
    )
    refunds = repository.list_refunds(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id=record.order_id,
    )
    return _public_order(
        record,
        payment=PublicPayment.from_record(payment) if payment is not None else None,
        refunds=tuple(PublicRefund.from_record(item) for item in refunds),
    )


def _private_identity(request: Request, *, mutation: bool) -> tuple[str, str]:
    """返回经过注册 Session 验证的用户和工作区 ID。"""

    access = require_registered_access(request, mutation=mutation)
    if mutation:
        raise api_error(403, "customer_data_read_only")
    user_id = access.principal.user_id
    if user_id is None:
        raise api_error(401, "authentication_required")
    return user_id, access.principal.workspace_id


@router.get("", response_model=OrdersResponse)
def list_orders(request: Request) -> OrdersResponse:
    """列出当前注册用户私有工作区中的订单与物流。"""

    services = get_services(request)
    user_id, workspace_id = _private_identity(request, mutation=False)
    records = services.repository.list_orders(
        user_id=user_id,
        workspace_id=workspace_id,
    )
    return OrdersResponse(
        orders=tuple(
            _public_order_with_transactions(
                request,
                user_id=user_id,
                workspace_id=workspace_id,
                record=item,
            )
            for item in records
        )
    )


@router.post("", response_model=PublicOrder, status_code=201)
def create_order(request: Request, payload: OrderCreate) -> PublicOrder:
    """在当前注册用户私有工作区创建订单及可选物流。"""

    services = get_services(request)
    user_id, workspace_id = _private_identity(request, mutation=True)
    try:
        record = services.repository.create_order(
            user_id=user_id,
            workspace_id=workspace_id,
            data=payload,
        )
    except BusinessDataError as error:
        status = 409 if error.error_code == "order_conflict" else 404
        raise api_error(status, error.error_code) from None
    return _public_order_with_transactions(
        request,
        user_id=user_id,
        workspace_id=workspace_id,
        record=record,
    )


@router.patch("/{order_id}", response_model=PublicOrder)
def update_order(
    order_id: str,
    request: Request,
    payload: OrderUpdate,
) -> PublicOrder:
    """更新当前工作区指定订单与一对一物流记录。"""

    services = get_services(request)
    user_id, workspace_id = _private_identity(request, mutation=True)
    try:
        record = services.repository.update_order(
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=order_id,
            data=payload,
        )
    except BusinessDataError as error:
        raise api_error(404, error.error_code) from None
    return _public_order_with_transactions(
        request,
        user_id=user_id,
        workspace_id=workspace_id,
        record=record,
    )


@router.put("/{order_id}/payment", response_model=PublicPayment)
def upsert_payment(
    order_id: str,
    request: Request,
    payload: MockPaymentInput,
) -> PublicPayment:
    """为当前注册用户订单创建或更新退款前 Mock 支付事实。"""

    services = get_services(request)
    user_id, workspace_id = _private_identity(request, mutation=True)
    try:
        record = services.require_refund_repository().upsert_payment(
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=order_id,
            data=payload,
        )
    except BusinessDataError as error:
        status = 409 if error.error_code == "payment_locked" else 404
        raise api_error(status, error.error_code) from None
    return PublicPayment.from_record(record)


@router.get("/{order_id}/refunds", response_model=RefundsResponse)
def list_refunds(order_id: str, request: Request) -> RefundsResponse:
    """列出当前注册用户指定私有订单的全部 Mock 退款结果。"""

    services = get_services(request)
    user_id, workspace_id = _private_identity(request, mutation=False)
    try:
        records = services.require_refund_repository().list_refunds(
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=order_id,
        )
    except BusinessDataError as error:
        raise api_error(404, error.error_code) from None
    return RefundsResponse(
        refunds=tuple(PublicRefund.from_record(item) for item in records)
    )


@router.delete("/{order_id}", response_model=DeleteResponse)
def delete_order(order_id: str, request: Request) -> DeleteResponse:
    """删除当前工作区指定订单及其物流，并统一越权错误。"""

    services = get_services(request)
    user_id, workspace_id = _private_identity(request, mutation=True)
    try:
        deleted = services.repository.delete_order(
            user_id=user_id,
            workspace_id=workspace_id,
            order_id=order_id,
        )
    except BusinessDataError as error:
        status = 409 if error.error_code == "order_has_transaction_data" else 404
        raise api_error(status, error.error_code) from None
    return DeleteResponse(deleted=deleted)
