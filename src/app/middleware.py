"""Authenticate before multipart parsing, cap actual streamed bytes and reject cross-origin writes."""
from urllib.parse import urlsplit
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.config import get_settings
from app.deps import require_access
from app.errors import AppError


class BodyTooLarge(AppError):
    def __init__(self):
        super().__init__(413, "REQUEST_TOO_LARGE", "请求体超过大小上限")


class RequestGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        settings = get_settings()
        try:
            if settings.local_only or (scope["path"].startswith("/api") and scope["path"] not in {"/api/auth/login", "/api/health"}):
                require_access(request, settings)
            if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
                origin = request.headers.get("origin")
                if origin and urlsplit(origin).netloc != request.url.netloc:
                    raise AppError(403, "ORIGIN_REJECTED", "不接受跨站写入")
        except AppError as exc:
            response = JSONResponse({"error": {"code": exc.code, "message": exc.detail}}, status_code=exc.status_code)
            return await response(scope, receive, send)
        if not scope["path"].startswith("/api"):
            return await self.app(scope, receive, send)
        limit = (3 * settings.max_upload_mb * 1024 * 1024 + 1024 * 1024
                 if scope["path"] == "/api/audits" and scope["method"] == "POST" else 2 * 1024 * 1024)
        try:
            if int(request.headers.get("content-length", "0")) > limit:
                raise BodyTooLarge()
        except ValueError:
            return await JSONResponse({"error": {"code": "INVALID_LENGTH", "message": "请求长度无效"}}, 400)(scope, receive, send)
        except BodyTooLarge:
            return await self.too_large(scope, receive, send)
        count = 0

        async def limited_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > limit:
                raise BodyTooLarge()
            return message
        try:
            return await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            return await self.too_large(scope, receive, send)

    async def too_large(self, scope, receive, send):
        await JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体超过大小上限"}}, 413)(scope, receive, send)
