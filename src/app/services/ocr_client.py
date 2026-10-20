"""Baidu REST with configured credentials, persistent quotas and safe errors."""
import base64
import json
import socket
import time
from threading import Lock
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import text
from app.config import get_settings
from app.db import Usage, init_db
from app.errors import AppError


class ServiceError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def ocr_budget_message(limit, used, required):
    return (f"本站 OCR 每日调用上限为 {limit} 次，已用 {used} 次，剩余 {max(0, limit - used)} 次，"
            f"本次至少需要 {required} 次；全空间访客共享，北京时间每天 08:00 重置。"
            "这是本站调用预算，不代表百度 OCR 额度已耗尽。")


def require_ocr_capacity(db, calls):
    day = datetime.now(timezone.utc).date().isoformat()
    usage = db.get(Usage, (day, "ocr"))
    used = usage.count if usage else 0
    limit = get_settings().daily_ocr_limit
    if used + calls > limit:
        raise AppError(429, "OCR_BUDGET", ocr_budget_message(limit, used, calls))


def reserve_usage(service, limit):
    with init_db()() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        day = datetime.now(timezone.utc).date().isoformat()
        count = db.execute(text("SELECT count FROM daily_usage WHERE day=:day AND service=:service"),
                           {"day": day, "service": service}).scalar() or 0
        if count >= limit:
            if service == "ocr":
                raise ServiceError("OCR_BUDGET", ocr_budget_message(limit, count, 1), True)
            raise ServiceError(service.upper() + "_QUOTA", "已达到服务每日调用上限")
        db.execute(text("INSERT INTO daily_usage(day,service,count) VALUES (:day,:service,1) "
                        "ON CONFLICT(day,service) DO UPDATE SET count=count+1"), {"day": day, "service": service})
        db.commit()


# All client instances in the single Worker share the same conservative pace.
# Keep below the free tier's 2 QPS even when several crops finish quickly.
_OCR_REQUEST_LOCK = Lock()
_next_ocr_request_at = 0.0


def pace_ocr_request():
    global _next_ocr_request_at
    with _OCR_REQUEST_LOCK:
        now = time.monotonic()
        if now < _next_ocr_request_at:
            time.sleep(_next_ocr_request_at - now)
        _next_ocr_request_at = time.monotonic() + 1.05


class BaiduClient:
    def __init__(self):
        self.token = None
        self.expires_at = 0

    def post(self, url, data):
        request = Request(url, data=urlencode(data).encode("ascii"),
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=35) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ServiceError("OCR_RESPONSE", "OCR 响应超过大小限制", True)
            result = json.loads(raw)
        except HTTPError as exc:
            code = "OCR_AUTH" if exc.code in {401, 403} else "OCR_RATE_LIMIT" if exc.code == 429 else "OCR_NETWORK"
            raise ServiceError(code, "OCR 服务 HTTP 错误 " + str(exc.code), exc.code == 429 or exc.code >= 500) from None
        except (TimeoutError, socket.timeout):
            raise ServiceError("OCR_TIMEOUT", "OCR 请求超时", True) from None
        except URLError as exc:
            timeout = isinstance(exc.reason, (TimeoutError, socket.timeout))
            raise ServiceError("OCR_TIMEOUT" if timeout else "OCR_NETWORK", "OCR 网络连接失败", True) from None
        except (ValueError, UnicodeError):
            raise ServiceError("OCR_RESPONSE", "OCR 返回无效 JSON", True) from None
        if not isinstance(result, dict):
            raise ServiceError("OCR_RESPONSE", "OCR 返回格式无效", True)
        error = result.get("error_code")
        if error or result.get("error"):
            if error == 18:
                raise ServiceError("OCR_RATE_LIMIT", "百度 OCR 调用过快（错误码 18，QPS 超限），请稍后重试", True)
            if error == 17:
                raise ServiceError("OCR_QUOTA", "百度 OCR 当日调用额度已用尽（错误码 17），请核对该接口的每日额度")
            if error == 19:
                raise ServiceError("OCR_QUOTA", "百度 OCR 总调用额度已用尽（错误码 19），请核对该接口的资源包")
            if error in {110, 111} or result.get("error") in {"invalid_client", "invalid_grant"}:
                self.token = None
                raise ServiceError("OCR_AUTH", "百度 OCR 认证失败，请检查密钥")
            raise ServiceError("OCR_SERVICE", "百度 OCR 请求失败，服务错误码 " + str(error or result.get("error")), True)
        return result

    def access_token(self):
        settings = get_settings()
        if not settings.ocr_configured:
            raise ServiceError("OCR_AUTH", "尚未配置百度 OCR 密钥")
        if not self.token or time.monotonic() >= self.expires_at:
            result = self.post("https://aip.baidubce.com/oauth/2.0/token", {
                "grant_type": "client_credentials", "client_id": settings.baidu_api_key,
                "client_secret": settings.baidu_secret_key})
            self.token = result.get("access_token")
            if not self.token:
                raise ServiceError("OCR_AUTH", "百度 OCR 未返回访问令牌")
            self.expires_at = time.monotonic() + max(1, int(result.get("expires_in", 3600)) - 60)
        return self.token

    def recognize(self, data, kind):
        token = self.access_token()
        params = {"image": base64.b64encode(data).decode("ascii")}
        if kind == "accurate":
            params.update(language_type="CHN_ENG", probability="true")
        elif kind == "table":
            params.update(cell_contents="true", return_excel="false")
        else:
            raise ValueError("Unknown OCR kind")
        for attempt in range(4):
            pace_ocr_request()
            # Count every attempted provider call, including retries.
            reserve_usage("ocr", get_settings().daily_ocr_limit)
            try:
                result = self.post("https://aip.baidubce.com/rest/2.0/ocr/v1/" + kind + "?access_token=" + token, params)
                break
            except ServiceError as exc:
                if exc.code != "OCR_RATE_LIMIT" or attempt == 3:
                    raise
                time.sleep(2 ** attempt)

        if kind == "accurate" and not isinstance(result.get("words_result"), list):
            raise ServiceError("OCR_RESPONSE", "高精度 OCR 响应缺少文本行", True)
        if kind == "table":
            if result.get("table_num") == 0:
                result.setdefault("tables_result", [])
            if not isinstance(result.get("table_num"), int) or not isinstance(result.get("tables_result"), list):
                raise ServiceError("OCR_RESPONSE", "表格 OCR 响应缺少表结构", True)
        return result
