"""提供管理员 Mock 数据、邀请、Monitoring、Eval 与系统状态 API。"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request

from commerce_resolve.adapters.sqlite_workspaces import WorkspaceDataError
from commerce_resolve.admin_models import (
    AdminAuditRecord,
    AdminCustomer,
    AdminEvalSnapshot,
    AdminInvitation,
    AdminOverview,
    AdminRunDetail,
    AdminRunSummary,
    AdminSystemSnapshot,
)
from commerce_resolve.admin_services import AdminEvalReader, build_system_snapshot
from commerce_resolve.auth import AuthDomainError
from commerce_resolve.business_models import InvitationIssued
from commerce_resolve.structured_logging import log_event
from commerce_resolve.workspace_models import WorkspaceResetResult
from commerce_resolve.workspace_reset import WorkspaceResetService

from ..dependencies import get_services, require_admin_access
from ..errors import api_error
from ..schemas import (
    AdminInvitationCreateRequest,
    DeleteResponse,
    WorkspaceResetRequest,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
LOGGER = logging.getLogger("commerce_resolve.admin")


def _admin_ids(request: Request, *, mutation: bool) -> tuple[str, str]:
    """返回已验证管理员 ID 与工作区，拒绝客户端自报身份。"""

    access = require_admin_access(request, mutation=mutation)
    if access.principal.user_id is None:
        raise api_error(403, "admin_access_required")
    return access.principal.user_id, access.principal.workspace_id


def _target(request: Request, user_id: str) -> AdminCustomer:
    """解析后台显式目标客户，不把管理员 Principal 替换成客户。"""

    target = get_services(request).require_admin_repository().get_customer(user_id)
    if target is None:
        raise api_error(404, "admin_target_unavailable")
    return target


def _audit(
    request: Request,
    *,
    admin_user_id: str,
    target_user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    result: str,
    parameters: dict[str, object],
) -> None:
    """保存后台写操作最小摘要；审计故障不得篡改已发生的业务结果。"""

    try:
        get_services(request).require_admin_repository().record_action(
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            parameter_summary=parameters,
        )
    except Exception:
        log_event(
            LOGGER,
            logging.ERROR,
            "admin_audit_write_failed",
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            action=action,
            resource_type=resource_type,
            result=result,
        )


@router.get("/customers", response_model=list[AdminCustomer])
def list_customers(request: Request) -> list[AdminCustomer]:
    """列出准备演示数据所需的有限注册账号信息。"""

    _admin_ids(request, mutation=False)
    return get_services(request).require_admin_repository().list_customers()


@router.post(
    "/customers/{user_id}/demo-workspace/reset",
    response_model=WorkspaceResetResult,
)
def reset_customer_demo_workspace(
    user_id: str,
    request: Request,
    payload: WorkspaceResetRequest,
) -> WorkspaceResetResult:
    """由管理员为明确目标客户执行同等的完整演示工作区重置。"""

    admin_user_id, _admin_workspace_id = _admin_ids(request, mutation=True)
    target = _target(request, user_id)
    services = get_services(request)
    with services.workspace_locks.acquire(target.workspace_id) as acquired:
        if not acquired:
            raise api_error(409, "workspace_reset_in_progress")
        reset_service = WorkspaceResetService(
            services.require_workspace_repository(),
            checkpoint_database=services.settings.checkpoint_db_path,
            memory_database=services.settings.memory_db_path,
        )
        try:
            result = reset_service.reset(
                owner_user_id=target.user_id,
                workspace_id=target.workspace_id,
                actor_user_id=admin_user_id,
                actor_role="admin",
                client_request_id=payload.client_request_id,
            )
        except WorkspaceDataError as error:
            _audit(
                request,
                admin_user_id=admin_user_id,
                target_user_id=target.user_id,
                action="demo_workspace.reset",
                resource_type="workspace",
                resource_id=target.workspace_id,
                result="failed",
                parameters={"client_request_id": payload.client_request_id},
            )
            status = 404 if error.error_code == "workspace_not_accessible" else 409
            raise api_error(status, error.error_code) from None
    _audit(
        request,
        admin_user_id=admin_user_id,
        target_user_id=target.user_id,
        action="demo_workspace.reset",
        resource_type="workspace",
        resource_id=target.workspace_id,
        result="succeeded",
        parameters={
            "client_request_id": payload.client_request_id,
            "reset_generation": result.reset_generation,
            "already_completed": result.already_completed,
        },
    )
    return result


@router.get("/invitations", response_model=list[AdminInvitation])
def list_invitations(request: Request) -> list[AdminInvitation]:
    """列出不含明文或 Hash 的邀请码使用状态。"""

    _admin_ids(request, mutation=False)
    return get_services(request).require_admin_repository().list_invitations()


@router.post(
    "/invitations",
    response_model=InvitationIssued,
    status_code=201,
)
def create_invitation(
    request: Request,
    payload: AdminInvitationCreateRequest,
) -> InvitationIssued:
    """创建邀请码并只在本次响应中返回明文。"""

    admin_user_id, _ = _admin_ids(request, mutation=True)
    try:
        invitation = get_services(request).repository.create_invitation(
            expires_in_hours=payload.expires_in_hours,
            max_uses=payload.max_uses,
        )
    except AuthDomainError as error:
        raise api_error(400, error.error_code) from None
    _audit(
        request,
        admin_user_id=admin_user_id,
        target_user_id=None,
        action="invitation.create",
        resource_type="invitation",
        resource_id=invitation.id,
        result="succeeded",
        parameters={
            "expires_in_hours": payload.expires_in_hours,
            "max_uses": payload.max_uses,
        },
    )
    return invitation


@router.delete("/invitations/{invitation_id}", response_model=DeleteResponse)
def revoke_invitation(invitation_id: str, request: Request) -> DeleteResponse:
    """幂等撤销邀请码，不在审计或响应中暴露明文。"""

    admin_user_id, _ = _admin_ids(request, mutation=True)
    revoked = get_services(request).repository.revoke_invitation(invitation_id)
    if not revoked:
        raise api_error(404, "admin_target_unavailable")
    _audit(
        request,
        admin_user_id=admin_user_id,
        target_user_id=None,
        action="invitation.revoke",
        resource_type="invitation",
        resource_id=invitation_id,
        result="succeeded",
        parameters={},
    )
    return DeleteResponse(deleted=True)


@router.get("/audit", response_model=list[AdminAuditRecord])
def list_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AdminAuditRecord]:
    """读取有界后台业务写审计，不读取凭证和客户消息。"""

    _admin_ids(request, mutation=False)
    return get_services(request).require_admin_repository().list_audit(limit=limit)


@router.get("/agent-runs", response_model=list[AdminRunSummary])
def list_agent_runs(
    request: Request,
    status: str | None = Query(default=None, max_length=24),
    request_kind: str | None = Query(default=None, max_length=32),
    started_after: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[AdminRunSummary]:
    """按有限条件读取真实 Agent Run 元数据，不连接消息正文。"""

    _admin_ids(request, mutation=False)
    return (
        get_services(request)
        .require_admin_repository()
        .list_runs(
            status=status,
            request_kind=request_kind,
            started_after=started_after,
            limit=limit,
        )
    )


@router.get("/agent-runs/{run_id}", response_model=AdminRunDetail)
def get_agent_run(run_id: str, request: Request) -> AdminRunDetail:
    """返回白名单 Run 事件和可选 L2 聚合诊断。"""

    _admin_ids(request, mutation=False)
    detail = get_services(request).require_admin_repository().get_run_detail(run_id)
    if detail is None:
        raise api_error(404, "admin_target_unavailable")
    return detail


def _eval_reader(request: Request) -> AdminEvalReader:
    """使用服务端固定配置构造只读 Eval Artifact Reader。"""

    settings = get_services(request).settings
    return AdminEvalReader(settings.eval_run_root, settings.eval_baseline_path)


@router.get("/eval", response_model=AdminEvalSnapshot)
def get_latest_eval(request: Request) -> AdminEvalSnapshot:
    """读取最近 Eval Candidate 和当前 Baseline，不启动 Eval。"""

    _admin_ids(request, mutation=False)
    return _eval_reader(request).latest()


@router.get("/eval/runs/{run_id}", response_model=AdminEvalSnapshot)
def get_eval_run(run_id: str, request: Request) -> AdminEvalSnapshot:
    """读取指定受限 Run ID 的 Eval 摘要，不接受文件路径。"""

    _admin_ids(request, mutation=False)
    return _eval_reader(request).read(run_id)


def _system(request: Request) -> AdminSystemSnapshot:
    """从 App State 构造不含路径和配置值的系统状态。"""

    return build_system_snapshot(
        request.app.state.deployment_settings,
        request.app.state.release_manifest,
    )


@router.get("/system", response_model=AdminSystemSnapshot)
def get_system(request: Request) -> AdminSystemSnapshot:
    """返回版本、迁移、健康、Capability 和存储有限状态。"""

    _admin_ids(request, mutation=False)
    return _system(request)


@router.get("/overview", response_model=AdminOverview)
def get_overview(request: Request) -> AdminOverview:
    """组合权威计数、最近 Run、Eval 与系统只读摘要。"""

    _admin_ids(request, mutation=False)
    repository = get_services(request).require_admin_repository()
    return AdminOverview(
        counts=repository.overview_counts(),
        recent_runs=tuple(repository.list_runs(limit=5)),
        evaluation=_eval_reader(request).latest(),
        system=_system(request),
    )
