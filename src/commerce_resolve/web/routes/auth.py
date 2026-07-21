"""提供游客 Session、邀请注册、登录和退出 API。"""

from fastapi import APIRouter, Request, Response

from commerce_resolve.auth import AuthDomainError

from ..dependencies import (
    build_session_response,
    enforce_rate_limit,
    get_services,
    require_mutation_access,
    require_registered_access,
    set_session_cookie,
)
from ..errors import api_error
from ..schemas import (
    LoginRequest,
    RegisterRequest,
    RegistrationResponse,
    SessionResponse,
)

router = APIRouter(prefix="/api", tags=["session"])


def _client_key(request: Request, route: str) -> str:
    """按客户端地址和路由构造不含凭据的单实例限流键。"""

    host = request.client.host if request.client is not None else "unknown"
    return f"{route}:{host}"


@router.get("/session", response_model=SessionResponse)
def get_session(request: Request, response: Response) -> SessionResponse:
    """恢复有效浏览器 Session，或创建独立游客 Session。"""

    services = get_services(request)
    token = request.cookies.get(services.settings.cookie_name)
    identity = services.repository.resolve_session(token) if token else None
    if token is not None and identity is not None:
        csrf_token = services.repository.rotate_csrf(token)
        if csrf_token is None:
            raise api_error(401, "authentication_required")
        return build_session_response(identity, csrf_token, services)

    bundle = services.repository.create_guest_session(
        ttl_hours=services.settings.guest_session_ttl_hours
    )
    set_session_cookie(
        response,
        services,
        bundle.session_token,
        expires_at=bundle.expires_at,
    )
    return build_session_response(bundle, bundle.csrf_token, services)


@router.post("/auth/register", response_model=RegistrationResponse, status_code=201)
def register(request: Request, payload: RegisterRequest) -> RegistrationResponse:
    """验证游客请求并原子消费邀请码创建账号与工作区。"""

    services = get_services(request)
    access = require_mutation_access(request)
    if access.principal.mode != "guest":
        raise api_error(409, "account_unavailable")
    enforce_rate_limit(services, _client_key(request, "register"), limit=10)
    try:
        result = services.repository.register(
            username=payload.username,
            password=payload.password,
            invitation_code=payload.invitation_code,
        )
    except AuthDomainError as error:
        code = (
            error.error_code
            if error.error_code in {"invitation_unavailable", "account_unavailable"}
            else "account_unavailable"
        )
        raise api_error(400, code) from None
    return RegistrationResponse(username=result.user.username)


@router.post("/auth/login", response_model=SessionResponse)
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
) -> SessionResponse:
    """验证账号后撤销游客 Session，并轮换为注册用户 Session。"""

    services = get_services(request)
    access = require_mutation_access(request)
    if access.principal.mode != "guest":
        raise api_error(409, "account_unavailable")
    enforce_rate_limit(services, _client_key(request, "login"), limit=12)
    try:
        registration = services.repository.authenticate(
            payload.username,
            payload.password,
        )
    except AuthDomainError:
        raise api_error(401, "authentication_failed") from None
    services.repository.revoke_session(access.session_token)
    bundle = services.repository.create_registered_session(
        registration,
        ttl_hours=services.settings.session_ttl_hours,
    )
    set_session_cookie(
        response,
        services,
        bundle.session_token,
        expires_at=bundle.expires_at,
    )
    return build_session_response(bundle, bundle.csrf_token, services)


@router.post("/auth/logout", response_model=SessionResponse)
def logout(request: Request, response: Response) -> SessionResponse:
    """撤销注册 Session，并为当前浏览器建立全新游客 Session。"""

    services = get_services(request)
    access = require_registered_access(request, mutation=True)
    services.repository.revoke_session(access.session_token)
    bundle = services.repository.create_guest_session(
        ttl_hours=services.settings.guest_session_ttl_hours
    )
    set_session_cookie(
        response,
        services,
        bundle.session_token,
        expires_at=bundle.expires_at,
    )
    return build_session_response(bundle, bundle.csrf_token, services)
