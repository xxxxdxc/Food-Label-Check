from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.contract import public_router, router
from app.config import get_settings
from app.errors import AppError
from app.schemas.contract import HealthResponse


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings()
    yield


app = FastAPI(title="Food-Label-Check", lifespan=lifespan)
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
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": str(exc.detail)}},
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
