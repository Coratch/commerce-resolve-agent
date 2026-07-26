"""提供售后首页、客户订单与统一服务进度的只读 API。"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from commerce_resolve.adapters.sqlite_service_center import (
    SqliteSupportCenterReader,
)
from commerce_resolve.service_center_models import (
    ServiceRecordDetail,
    SupportOrderDetail,
    SupportOrdersPage,
    SupportOverview,
    SupportServicesPage,
)

from ..dependencies import RequestAccess, get_services, require_registered_access
from ..errors import api_error

router = APIRouter(prefix="/api/support", tags=["support-center"])


def _encode_cursor(
    updated_at: datetime,
    resource_id: str,
    *,
    binding: str | None = None,
) -> str:
    """把稳定排序键编码为不含身份信息的 URL-safe 游标。"""

    payload = json.dumps(
        {
            "updated_at": updated_at.isoformat(),
            "resource_id": resource_id,
            "binding": binding,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(
    cursor: str | None,
    *,
    binding: str | None = None,
) -> tuple[datetime, str] | None:
    """解析并严格校验分页游标，格式错误统一返回公开请求错误。"""

    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        resource_id = payload["resource_id"]
        if (
            updated_at.tzinfo is None
            or not isinstance(resource_id, str)
            or payload.get("binding") != binding
        ):
            raise ValueError
        return updated_at.astimezone(UTC), resource_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise api_error(422, "invalid_cursor") from None


def _registered_reader(
    request: Request,
) -> tuple[SqliteSupportCenterReader, RequestAccess]:
    """返回当前注册身份和共享业务库只读投影器。"""

    access = require_registered_access(request, mutation=False)
    services = get_services(request)
    return SqliteSupportCenterReader(services.repository.engine), access


@router.get("/overview", response_model=SupportOverview)
def get_support_overview(request: Request) -> SupportOverview:
    """返回注册客户售后首页；读取不会创建会话或调用模型。"""

    access = require_registered_access(request, mutation=False)
    services = get_services(request)
    user_id = access.principal.user_id
    if user_id is None:
        raise api_error(401, "authentication_required")
    return SqliteSupportCenterReader(services.repository.engine).overview(
        subject_id=access.identity.subject_id,
        user_id=user_id,
        workspace_id=access.principal.workspace_id,
    )


@router.get("/orders", response_model=SupportOrdersPage)
def list_support_orders(
    request: Request,
    q: Annotated[str | None, Query(max_length=80)] = None,
    view: Annotated[
        Literal["all", "processing", "shipping", "delivered", "after_sales"],
        Query(),
    ] = "all",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> SupportOrdersPage:
    """按搜索词和客户状态分页列出当前身份的订单。"""

    access = require_registered_access(request, mutation=False)
    services = get_services(request)
    normalized_query = q.strip() if q is not None else ""
    binding = json.dumps(
        {"q": normalized_query.casefold(), "view": view},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    before = _decode_cursor(cursor, binding=binding)
    user_id = access.principal.user_id
    if user_id is None:
        raise api_error(401, "authentication_required")
    orders = SqliteSupportCenterReader(services.repository.engine).list_orders(
        user_id=user_id,
        workspace_id=access.principal.workspace_id,
        limit=limit + 1,
        before=before,
        q=normalized_query,
        view=view,
    )
    page = orders[:limit]
    next_cursor = (
        _encode_cursor(
            page[-1].updated_at,
            page[-1].order_id,
            binding=binding,
        )
        if len(orders) > limit and page
        else None
    )
    return SupportOrdersPage(orders=tuple(page), next_cursor=next_cursor)


@router.get("/orders/{order_id}", response_model=SupportOrderDetail)
def get_support_order(order_id: str, request: Request) -> SupportOrderDetail:
    """返回当前身份有权访问的订单详情，越权与不存在统一为 404。"""

    access = require_registered_access(request, mutation=False)
    services = get_services(request)
    user_id = access.principal.user_id
    detail = (
        SqliteSupportCenterReader(services.repository.engine).get_order(
            user_id=user_id,
            workspace_id=access.principal.workspace_id,
            order_id=order_id,
        )
        if user_id is not None
        else None
    )
    if detail is None:
        raise api_error(404, "order_not_accessible")
    return detail


@router.get("/services", response_model=SupportServicesPage)
def list_support_services(
    request: Request,
    view: Annotated[Literal["active", "history"], Query()] = "active",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> SupportServicesPage:
    """分页列出当前注册客户的退款与 AI 深度处理服务投影。"""

    access = require_registered_access(request, mutation=False)
    user_id = access.principal.user_id
    if user_id is None:
        raise api_error(401, "authentication_required")
    records = SqliteSupportCenterReader(
        get_services(request).repository.engine
    ).list_services(
        subject_id=access.identity.subject_id,
        user_id=user_id,
        workspace_id=access.principal.workspace_id,
        view=view,
        limit=limit + 1,
        before=_decode_cursor(cursor),
    )
    page = records[:limit]
    next_cursor = (
        _encode_cursor(page[-1].updated_at, page[-1].service_id)
        if len(records) > limit and page
        else None
    )
    return SupportServicesPage(services=tuple(page), next_cursor=next_cursor)


@router.get("/services/{service_id}", response_model=ServiceRecordDetail)
def get_support_service(service_id: str, request: Request) -> ServiceRecordDetail:
    """返回本人单条服务详情；越权与不存在使用统一语义。"""

    reader, access = _registered_reader(request)
    principal = access.principal
    if principal.user_id is None:
        raise api_error(401, "authentication_required")
    detail = reader.get_service(
        service_id=service_id,
        subject_id=access.identity.subject_id,
        user_id=principal.user_id,
        workspace_id=principal.workspace_id,
    )
    if detail is None:
        raise api_error(404, "service_not_accessible")
    return detail
