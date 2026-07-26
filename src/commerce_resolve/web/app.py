"""创建 FastAPI 模块化单体并统一安全响应与异常边界。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from commerce_resolve.operations.manifest import development_release_manifest
from commerce_resolve.operations.models import ReleaseManifest
from commerce_resolve.structured_logging import log_event

from .dependencies import WebServices, create_default_services
from .errors import ApiError
from .health import register_health_routes
from .request_context import RequestContextMiddleware
from .routes import (
    admin_router,
    auth_router,
    chat_router,
    conversations_router,
    l2_router,
    support_router,
    workspace_router,
)
from .settings import DeploymentSettings, WebSettings
from .spa import register_spa_routes


class SecurityHeadersMiddleware:
    """为所有 HTTP 响应附加同源 Web 所需的最小安全 Header。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游 ASGI 应用。"""

        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """包装响应开始事件并追加 CSP、frame 与嗅探防护。"""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            """仅修改响应开始事件，不读取或记录响应正文。"""

            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    (
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"same-origin"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self'; "
                            b"img-src 'self' data:; connect-src 'self'",
                        ),
                    )
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def handle_api_error(_request: Request, error: ApiError) -> JSONResponse:
    """把 Web 领域异常转换为稳定且不含内部上下文的 JSON。"""

    return JSONResponse(
        status_code=error.status_code,
        content={"error_code": error.error_code, "message": error.message},
    )


async def handle_validation_error(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    """返回不包含密码、邀请码或原始输入值的 Schema 错误。"""

    details = [
        {
            "location": [str(item) for item in issue["loc"]],
            "type": issue["type"],
            "message": issue["msg"],
        }
        for issue in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "validation_failed",
            "message": "请求数据不符合约束。",
            "details": details,
        },
    )


async def handle_unexpected_error(
    _request: Request,
    _error: Exception,
) -> JSONResponse:
    """隐藏堆栈、路径、数据库和模型细节并返回统一服务错误。"""

    return JSONResponse(
        status_code=500,
        content={
            "error_code": "internal_error",
            "message": "服务暂时不可用，请稍后重试。",
        },
    )


def _build_lifespan(services: WebServices):
    """创建负责重启收敛、停机收口和资源释放的 FastAPI lifespan。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """启动时收敛遗留 Run，结束时中断超时 Run 并释放连接。"""

        logger = logging.getLogger("commerce_resolve.lifecycle")
        try:
            startup_count = (
                services.require_conversation_repository().interrupt_unfinished_runs()
            )
            log_event(
                logger,
                logging.INFO,
                "lifecycle.reconciled",
                interrupted_runs=startup_count,
            )
            yield
        finally:
            shutdown_count = (
                services.require_conversation_repository().interrupt_unfinished_runs()
            )
            log_event(
                logger,
                logging.INFO,
                "lifecycle.shutdown_reconciled",
                interrupted_runs=shutdown_count,
            )
            services.dispose()

    return lifespan


def create_app(
    settings: WebSettings | None = None,
    services: WebServices | None = None,
    *,
    mount_spa: bool = True,
    deployment_settings: DeploymentSettings | None = None,
    release_manifest: ReleaseManifest | None = None,
) -> FastAPI:
    """创建可注入 Fake 依赖、部署配置和发布清单的 FastAPI 应用。"""

    selected_settings = services.settings if services is not None else settings
    selected_settings = selected_settings or WebSettings.from_env()
    selected_services = services or create_default_services(selected_settings)
    selected_deployment = deployment_settings or DeploymentSettings.from_env(
        selected_settings
    )
    project_root = Path(__file__).resolve().parents[3]
    selected_release = release_manifest or development_release_manifest(project_root)
    app = FastAPI(
        title="CommerceResolve Internal API",
        version=selected_release.app_version,
        docs_url=None,
        redoc_url=None,
        lifespan=_build_lifespan(selected_services),
    )
    app.state.services = selected_services
    app.state.deployment_settings = selected_deployment
    app.state.release_manifest = selected_release
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        handle_validation_error,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        Exception,
        handle_unexpected_error,  # type: ignore[arg-type]
    )
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(chat_router)
    app.include_router(conversations_router)
    app.include_router(l2_router)
    app.include_router(support_router)
    app.include_router(workspace_router)
    register_health_routes(app, selected_deployment, selected_release)

    if mount_spa:
        register_spa_routes(app, selected_settings.frontend_dist_path)
    return app


def openapi_document(app: FastAPI) -> dict[str, Any]:
    """返回供前端类型生成使用的稳定 OpenAPI 文档。"""

    return app.openapi()
