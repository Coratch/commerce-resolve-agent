"""提供注册客户本人演示工作区的状态和完整重置 API。"""

from fastapi import APIRouter, Request

from commerce_resolve.adapters.sqlite_workspaces import WorkspaceDataError
from commerce_resolve.workspace_models import (
    DemoWorkspaceStatus,
    WorkspaceResetResult,
)
from commerce_resolve.workspace_reset import WorkspaceResetService

from ..dependencies import get_services, require_registered_access
from ..errors import api_error
from ..schemas import WorkspaceResetRequest

router = APIRouter(prefix="/api/demo-workspace", tags=["demo-workspace"])


def _reset_service(request: Request) -> WorkspaceResetService:
    """使用应用固定数据库路径装配跨存储重置服务。"""

    services = get_services(request)
    return WorkspaceResetService(
        services.require_workspace_repository(),
        checkpoint_database=services.settings.checkpoint_db_path,
        memory_database=services.settings.memory_db_path,
    )


@router.get("", response_model=DemoWorkspaceStatus)
def get_demo_workspace(request: Request) -> DemoWorkspaceStatus:
    """返回当前注册客户的演示数据版本和初始化状态。"""

    access = require_registered_access(request, mutation=False)
    user_id = access.principal.user_id
    if user_id is None:
        raise api_error(401, "authentication_required")
    status = (
        get_services(request)
        .require_workspace_repository()
        .get_status(
            user_id=user_id,
            workspace_id=access.principal.workspace_id,
        )
    )
    if status is None:
        raise api_error(404, "workspace_not_accessible")
    return status


@router.post("/reset", response_model=WorkspaceResetResult)
def reset_demo_workspace(
    request: Request,
    payload: WorkspaceResetRequest,
) -> WorkspaceResetResult:
    """经显式确认重置本人完整演示工作区，不接受局部事实参数。"""

    access = require_registered_access(request, mutation=True)
    user_id = access.principal.user_id
    role = access.principal.role
    if user_id is None or role is None:
        raise api_error(401, "authentication_required")
    services = get_services(request)
    with services.workspace_locks.acquire(access.principal.workspace_id) as acquired:
        if not acquired:
            raise api_error(409, "workspace_reset_in_progress")
        try:
            return _reset_service(request).reset(
                owner_user_id=user_id,
                workspace_id=access.principal.workspace_id,
                actor_user_id=user_id,
                actor_role=role,
                client_request_id=payload.client_request_id,
            )
        except WorkspaceDataError as error:
            status = 404 if error.error_code == "workspace_not_accessible" else 409
            raise api_error(status, error.error_code) from None
