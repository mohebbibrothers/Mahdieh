"""میان‌افزارهای هسته: زمینهٔ درخواست، لاگ دسترسی، و هدرهای امنیتی.

اصل: هر پاسخ باید قابل‌ردیابی باشد (شناسهٔ درخواست + ترِیس W3C)، و لاگ هر
درخواست باید زمان و تعداد کوئری را بداند تا N+1 و کندی زودتر لو برود.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
import logging
import re
import time
from typing import Any

from django.db import connection
from django.http import HttpRequest, HttpResponse

from apps.core.uuid7 import uuid7_hex

logger = logging.getLogger("mahdieh.access")

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

_REQUEST_ID_HEADER = "X-Request-Id"
_TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-(?:[0-9a-f]{2})", re.IGNORECASE)
_REQUEST_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")


def get_current_request_id() -> str | None:
    """شناسهٔ درخواست جاری (برای لاگ و خطاها)."""
    return _request_id_var.get()


def get_current_trace_id() -> str | None:
    """شناسهٔ ترِیس توزیع‌شدهٔ جاری (W3C Trace Context)."""
    return _trace_id_var.get()


class RequestContextMiddleware:
    """شناسهٔ درخواست و ترِیس را در سراسر پردازش در دسترس می‌گذارد."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """دستگیرهٔ زنجیرهٔ میان‌افزار."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """ساخت زمینه، اجرای زنجیره، و تزریق هدرها در پاسخ."""
        incoming = request.headers.get(_REQUEST_ID_HEADER, "")
        request_id = incoming if _REQUEST_ID_SAFE_RE.match(incoming) else uuid7_hex()

        trace_id: str | None = None
        traceparent = request.headers.get("traceparent", "")
        match = _TRACEPARENT_RE.match(traceparent)
        if match:
            trace_id = match.group(1)

        request_id_token = _request_id_var.set(request_id)
        trace_token = _trace_id_var.set(trace_id)

        # در دسترس گذاشتن برای نماها، فرم‌ها و آزمون‌ها
        request.request_id = request_id  # type: ignore[attr-defined]
        request.trace_id = trace_id  # type: ignore[attr-defined]

        try:
            response = self.get_response(request)
        finally:
            _request_id_var.reset(request_id_token)
            _trace_id_var.reset(trace_token)

        response[_REQUEST_ID_HEADER] = request_id
        if trace_id:
            response["traceparent"] = traceparent
        return response


class AccessLogMiddleware:
    """ثبت هر درخواست با زمان، تعداد کوئری و شناسهٔ درخواست."""

    # مسیرهایی که لاگ‌کردنشان نویز است
    SKIP_PREFIXES = ("/static/", "/media/", "/health/live", "/favicon.ico")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """دستگیرهٔ زنجیرهٔ میان‌افزار."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """اندازه‌گیری و ثبت فرادادهٔ درخواست."""
        if request.path.startswith(self.SKIP_PREFIXES):
            return self.get_response(request)

        started = time.perf_counter()
        response = self.get_response(request)
        duration_ms = (time.perf_counter() - started) * 1000

        query_count = len(connection.queries) if connection.force_debug_cursor else None

        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "query_count": query_count,
                "user_id": str(getattr(request.user, "pk", "") or ""),
            },
        )

        response["X-Response-Time-ms"] = f"{duration_ms:.2f}"
        if query_count is not None:
            response["X-DB-Query-Count"] = str(query_count)
        return response


class SecurityHeadersMiddleware:
    """افزودن هدرهای امنیتی تکمیلی که جنگو به‌صورت پیش‌فرض نمی‌گذارد."""

    EXTRA_HEADERS: dict[str, str] = {
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Permitted-Cross-Domain-Policies": "none",
        "X-DNS-Prefetch-Control": "off",
    }

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """دستگیرهٔ زنجیرهٔ میان‌افزار."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """تزریق هدرهای تکمیلی در پاسخ."""
        response = self.get_response(request)
        for header, value in self.EXTRA_HEADERS.items():
            response.setdefault(header, value)
        return response


def client_ip(request: HttpRequest) -> str:
    """استخراج آی‌پی واقعی کاربر با احتساب پروکسی معتبر.

    نکتهٔ امنیتی: ``X-Forwarded-For`` تنها وقتی معتبر است که پروکسی ما آن را
    تنظیم کند؛ پس فقط نخستین مقدار غیرقابل‌اعتماد را با احتیاط می‌خوانیم.
    """
    forwarded: str = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        first = forwarded.split(",", maxsplit=1)[0].strip()
        if first:
            return first
    remote: str = request.META.get("REMOTE_ADDR", "")
    return remote


def request_metadata(request: HttpRequest) -> dict[str, Any]:
    """فرادادهٔ درخواست برای حسابرسی (بدون دادهٔ حساس)."""
    return {
        "ip": client_ip(request),
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
        "method": request.method,
        "path": request.path,
        "request_id": get_current_request_id(),
    }
