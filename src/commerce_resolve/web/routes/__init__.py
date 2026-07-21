"""汇总 v0.3 同源内部 JSON API 路由。"""

from .auth import router as auth_router
from .chat import router as chat_router
from .conversations import router as conversations_router
from .l2 import router as l2_router
from .orders import router as orders_router

__all__ = [
    "auth_router",
    "chat_router",
    "conversations_router",
    "l2_router",
    "orders_router",
]
