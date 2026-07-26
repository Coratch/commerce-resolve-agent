"""汇总 v0.3 同源内部 JSON API 路由。"""

from .admin import router as admin_router
from .auth import router as auth_router
from .chat import router as chat_router
from .conversations import router as conversations_router
from .l2 import router as l2_router
from .support import router as support_router
from .workspace import router as workspace_router

__all__ = [
    "auth_router",
    "admin_router",
    "chat_router",
    "conversations_router",
    "l2_router",
    "support_router",
    "workspace_router",
]
