from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse

from app.api.contract import public_router, router
from app.config import get_settings
from app.errors import AppError
from app.schemas.contract import HealthResponse
from app.middleware import RequestGuard
from app.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings()
    init_db()
    from app.services.regulation import seed_clauses
    seed_clauses()
    yield


app = FastAPI(title="Food-Label-Check", lifespan=lifespan)
app.add_middleware(RequestGuard)
app.include_router(public_router)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "请求参数无效"}},
    )


@app.exception_handler(AppError)
async def app_error(_request: Request, exc: AppError) -> JSONResponse:
    error = {"code": exc.code, "message": str(exc.detail)}
    if hasattr(exc, "review_pending"):
        error["review_pending"] = exc.review_pending
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
    )


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        ocr_configured=settings.ocr_configured,
    )


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/") or path == "api":
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "接口不存在"}}, status_code=404)
    root = get_settings().frontend_dist.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "文件不存在"}}, status_code=404)
    if target.is_relative_to(get_settings().data_dir.resolve()):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "文件不存在"}}, status_code=404)
    if target.is_file():
        return FileResponse(target)
    if (root / "index.html").is_file() and "." not in path.rsplit("/", 1)[-1]:
        return FileResponse(root / "index.html")
    return JSONResponse({"error": {"code": "NOT_FOUND", "message": "页面不存在"}}, status_code=404)
