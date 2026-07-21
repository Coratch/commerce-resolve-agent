"""提供本人 L2 Case 公开轨迹与受限长期偏好管理 API。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from commerce_resolve.l2_memory import (
    correct_preference,
    delete_preference,
    list_preferences,
    open_sqlite_memory_store,
)

from ..dependencies import get_services, require_registered_access
from ..errors import api_error
from ..schemas import (
    DeleteResponse,
    L2CasesResponse,
    MemoriesResponse,
    MemoryUpdateRequest,
    PublicCustomerPreference,
    PublicL2CaseDetail,
    PublicL2CaseMetrics,
    PublicL2CaseSummary,
    PublicL2TraceEvent,
    PublicL2TracePage,
)

router = APIRouter(prefix="/api", tags=["l2-support"])


def _registered_scope(request: Request, *, mutation: bool):
    """返回已验证注册用户作用域，不接受客户端传入身份字段。"""

    access = require_registered_access(request, mutation=mutation)
    if access.principal.user_id is None:
        raise api_error(401, "authentication_required")
    return access


@router.get("/l2-cases", response_model=L2CasesResponse)
def list_l2_cases(
    request: Request,
    thread_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> L2CasesResponse:
    """按最近更新时间列出当前账号自己的 L2 Case。"""

    access = _registered_scope(request, mutation=False)
    records = (
        get_services(request)
        .require_l2_repository()
        .list_authorized_cases(
            subject_id=access.identity.subject_id,
            user_id=access.principal.user_id,
            workspace_id=access.principal.workspace_id,
            thread_id=thread_id,
            limit=limit,
        )
    )
    return L2CasesResponse(
        cases=tuple(PublicL2CaseSummary.from_domain(record) for record in records)
    )


@router.get("/l2-cases/{case_id}", response_model=PublicL2CaseDetail)
def get_l2_case(case_id: str, request: Request) -> PublicL2CaseDetail:
    """返回本人指定 Case 及脱敏公开轨迹，越权与不存在统一为 404。"""

    access = _registered_scope(request, mutation=False)
    repository = get_services(request).require_l2_repository()
    record = repository.get_authorized_case(
        case_id=case_id,
        subject_id=access.identity.subject_id,
        user_id=access.principal.user_id,
        workspace_id=access.principal.workspace_id,
    )
    if record is None:
        raise api_error(404, "l2_case_not_accessible")
    page = repository.list_events(
        case_id=case_id,
        user_id=access.principal.user_id,
        workspace_id=access.principal.workspace_id,
        limit=51,
    )
    events = page[:50]
    metrics = repository.get_case_metrics(
        case_id=case_id,
        user_id=access.principal.user_id,
        workspace_id=access.principal.workspace_id,
    )
    if metrics is None:
        raise api_error(404, "l2_case_not_accessible")
    return PublicL2CaseDetail(
        case=PublicL2CaseSummary.from_domain(record),
        events=tuple(PublicL2TraceEvent.from_domain(event) for event in events),
        metrics=PublicL2CaseMetrics.from_domain(metrics),
        next_after_sequence=(events[-1].sequence_no if len(page) > 50 else None),
        has_more=len(page) > 50,
    )


@router.get("/l2-cases/{case_id}/trace", response_model=PublicL2TracePage)
def get_l2_case_trace(
    case_id: str,
    request: Request,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PublicL2TracePage:
    """按 Case 内单调序号只读回放本人公开 Trace，不执行 Graph。"""

    access = _registered_scope(request, mutation=False)
    repository = get_services(request).require_l2_repository()
    record = repository.get_authorized_case(
        case_id=case_id,
        subject_id=access.identity.subject_id,
        user_id=access.principal.user_id,
        workspace_id=access.principal.workspace_id,
    )
    if record is None:
        raise api_error(404, "l2_case_not_accessible")
    page = repository.list_events(
        case_id=case_id,
        user_id=access.principal.user_id,
        workspace_id=access.principal.workspace_id,
        after_sequence=after_sequence,
        limit=limit + 1,
    )
    events = page[:limit]
    return PublicL2TracePage(
        case_id=case_id,
        trace_state=record.trace_state,
        events=tuple(PublicL2TraceEvent.from_domain(event) for event in events),
        next_after_sequence=(events[-1].sequence_no if len(page) > limit else None),
        has_more=len(page) > limit,
    )


@router.get("/memories", response_model=MemoriesResponse)
def list_memories(request: Request) -> MemoriesResponse:
    """从当前账号独立 namespace 返回最多三类已确认偏好。"""

    access = _registered_scope(request, mutation=False)
    services = get_services(request)
    with open_sqlite_memory_store(services.settings.memory_db_path) as store:
        records = list_preferences(
            store,
            user_id=access.principal.user_id,
            workspace_id=access.principal.workspace_id,
        )
    return MemoriesResponse(
        memories=tuple(
            PublicCustomerPreference.from_domain(record) for record in records
        )
    )


@router.patch("/memories/{memory_id}", response_model=PublicCustomerPreference)
def update_memory(
    memory_id: str,
    request: Request,
    payload: MemoryUpdateRequest,
) -> PublicCustomerPreference:
    """只纠正本人既有偏好的受限枚举值，并保留来源 Case。"""

    access = _registered_scope(request, mutation=True)
    services = get_services(request)
    try:
        with open_sqlite_memory_store(services.settings.memory_db_path) as store:
            record = correct_preference(
                store,
                user_id=access.principal.user_id,
                workspace_id=access.principal.workspace_id,
                memory_id=memory_id,
                value=payload.value,
            )
    except ValueError:
        raise api_error(422, "memory_value_invalid") from None
    if record is None:
        raise api_error(404, "memory_not_accessible")
    return PublicCustomerPreference.from_domain(record)


@router.delete("/memories/{memory_id}", response_model=DeleteResponse)
def remove_memory(memory_id: str, request: Request) -> DeleteResponse:
    """删除本人指定偏好；越权或不存在不泄露其他 namespace。"""

    access = _registered_scope(request, mutation=True)
    services = get_services(request)
    with open_sqlite_memory_store(services.settings.memory_db_path) as store:
        deleted = delete_preference(
            store,
            user_id=access.principal.user_id,
            workspace_id=access.principal.workspace_id,
            memory_id=memory_id,
        )
    if not deleted:
        raise api_error(404, "memory_not_accessible")
    return DeleteResponse(deleted=True)
