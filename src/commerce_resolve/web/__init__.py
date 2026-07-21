"""提供 CommerceResolve 的 FastAPI Web 产品入口。"""

from commerce_resolve.web.app import create_app

__all__ = ["create_app"]
