"""为每个 HTTP 请求生成或接受安全的 request_id。"""

from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from commerce_resolve.structured_logging import log_event, request_id_var

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
LOGGER = logging.getLogger("commerce_resolve.http")


class RequestContextMiddleware:
    """传播 request_id、响应 Header，并记录有限请求完成事实。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游 ASGI 应用。"""

        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """为 HTTP 请求建立 ContextVar，其他协议原样透传。"""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        supplied = headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid4().hex
        token = request_id_var.set(request_id)
        started = time.monotonic()
        status_code = 500

        async def send_with_context(message: Message) -> None:
            """追加响应关联 Header，并捕获最终 HTTP 状态。"""

            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            route = scope.get("route")
            log_event(
                LOGGER,
                logging.INFO,
                "http.request.completed",
                method=scope.get("method"),
                route=getattr(route, "path", "unmatched"),
                status_code=status_code,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
            request_id_var.reset(token)
