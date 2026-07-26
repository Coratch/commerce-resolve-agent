"""提供匿名状态、邀请注册、登录和退出 API。"""

from fastapi import APIRouter, Request, Response

from commerce_resolve.auth import AuthDomainError

from ..dependencies import (
    build_anonymous_session_response,
    build_session_response,
    enforce_rate_limit,
    get_services,
    require_public_mutation_origin,
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
    """恢复有效注册 Session；匿名访问不创建 Cookie 或业务身份。"""

    services = get_services(request)
    token = request.cookies.get(services.settings.cookie_name)
    identity = services.repository.resolve_session(token) if token else None
    if (
        token is not None
        and identity is not None
        and identity.actor_type == "registered"
    ):
        csrf_token = services.repository.rotate_csrf(token)
        if csrf_token is None:
            raise api_error(401, "authentication_required")
        return build_session_response(identity, csrf_token, services)
    if token is not None:
        services.repository.revoke_session(token)
        response.delete_cookie(
            services.settings.cookie_name,
            path="/",
            secure=services.settings.cookie_secure,
            samesite="lax",
        )
    return build_anonymous_session_response()


@router.post("/auth/register", response_model=RegistrationResponse, status_code=201)
def register(request: Request, payload: RegisterRequest) -> RegistrationResponse:
    """验证同源请求并原子创建账号、工作区与演示数据。"""

    services = get_services(request)
    require_public_mutation_origin(request)
    token = request.cookies.get(services.settings.cookie_name)
    if token is not None and services.repository.resolve_session(token) is not None:
        raise api_error(409, "account_unavailable")
    enforce_rate_limit(services, _client_key(request, "register"), limit=10)
    try:
        result = services.repository.register(
            username=payload.username,
            password=payload.password,
            invitation_code=payload.invitation_code,
        )
    except AuthDomainError as error:
        if error.error_code == "registration_initialization_failed":
            raise api_error(503, error.error_code) from None
        code = (
            error.error_code
            if error.error_code
            in {
                "invitation_unavailable",
                "account_unavailable",
            }
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
    """验证账号后创建或轮换注册用户 Session。"""

    services = get_services(request)
    require_public_mutation_origin(request)
    existing_token = request.cookies.get(services.settings.cookie_name)
    existing = (
        services.repository.resolve_session(existing_token)
        if existing_token is not None
        else None
    )
    if existing is not None and existing.actor_type == "registered":
        raise api_error(409, "account_unavailable")
    enforce_rate_limit(services, _client_key(request, "login"), limit=12)
    try:
        registration = services.repository.authenticate(
            payload.username,
            payload.password,
        )
    except AuthDomainError:
        raise api_error(401, "authentication_failed") from None
    if existing_token is not None:
        services.repository.revoke_session(existing_token)
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
    """撤销注册 Session、删除 Cookie 并返回匿名状态。"""

    services = get_services(request)
    access = require_registered_access(request, mutation=True)
    services.repository.revoke_session(access.session_token)
    response.delete_cookie(
        services.settings.cookie_name,
        path="/",
        secure=services.settings.cookie_secure,
        samesite="lax",
    )
    return build_anonymous_session_response()
