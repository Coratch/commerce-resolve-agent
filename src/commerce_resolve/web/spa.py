"""在生产构建存在时托管静态资源并支持 React Router fallback。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


def register_spa_routes(app: FastAPI, dist_path: Path) -> None:
    """把 Vite 产物挂载到 API 路由之后，缺失构建时保留明确 404。"""

    assets_path = dist_path / "assets"
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """为前端路径返回同一个 index.html，且不吞掉缺失的 API。"""

        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"error_code": "not_found", "message": "接口不存在。"},
            )
        index_path = dist_path / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "frontend_not_built",
                "message": "前端尚未构建，请使用 Vite 开发服务或先执行 npm run build。",
            },
        )
