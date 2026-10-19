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

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from apps.core.infrastructure import (
    RETRY_AFTER_SECONDS,
    SERVICE_UNAVAILABLE_STATUS,
    is_transient_infrastructure_error,
    service_unavailable_problem_detail,
)
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
    """آی‌پی واقعی کاربر — فقط با اعتماد به پروکسی‌هایی که خودمان گذاشته‌ایم.

    ``X-Forwarded-For`` زنجیره‌ای است که هر کس می‌تواند ابتدایش را جعل کند؛ تنها
    ``N`` مقدارِ **آخر** را پروکسی‌های ما افزوده‌اند (``TRUSTED_PROXY_COUNT``).
    با N=0 (پیش‌فرض) هدر کلاً نادیده گرفته می‌شود. همان قاعدهٔ ``NUM_PROXIES``
    در DRF است، تا سقف نرخ و حسابرسی هرگز دو آی‌پی متفاوت نبینند.
    """
    remote: str = request.META.get("REMOTE_ADDR", "")
    trusted = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0))
    if trusted <= 0:
        return remote
    forwarded: str = request.META.get("HTTP_X_FORWARDED_FOR", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if not hops:
        return remote
    return hops[-min(trusted, len(hops))]


def request_metadata(request: HttpRequest) -> dict[str, Any]:
    """فرادادهٔ درخواست برای حسابرسی (بدون دادهٔ حساس)."""
    return {
        "ip": client_ip(request),
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
        "method": request.method,
        "path": request.path,
        "request_id": get_current_request_id(),
    }


class DatabaseResilienceMiddleware:
    """تبدیل «قطعیِ زیرساخت» به پاسخِ محترمانهٔ ۵۰۳، نه ۵۰۰ خام.

    چرا؟ چون تفاوت این دو، تفاوت «سامانه خراب است» و «چند لحظه بعد دوباره
    تلاش کنید» است — هم برای زائر، هم برای موتور جست‌وجو، هم برای پایش.
    ۵۰۰ یعنی باگِ ما؛ ۵۰۳ یعنی ظرفیت/اتصالِ بیرونی. و بارسنجی فاز ۸ نشان داد
    وقتی پایگاه‌داده اتصال نمی‌دهد، صفحهٔ ۵۰۰ به کاربر نشان داده می‌شود؛ این
    میان‌افزار همان شکاف را می‌بندد:

    • خطای اتصال به پایگاه‌داده ⇒ صفحهٔ آرام فارسی + ``Retry-After``
    • مسیرهای API ⇒ بدنهٔ Problem Details (RFC 9457) با کد ``service_unavailable``
    • رویداد ``infrastructure_degraded`` در لاگ با شناسهٔ درخواست برای هشدارسازی

    کوتاه‌ترین زمان‌ها: این لایه هیچ کوئری اضافه‌ای اجرا نمی‌کند و در مسیر گرم
    تنها یک ``try/except`` است.
    """

    SERVICE_UNAVAILABLE_STATUS = SERVICE_UNAVAILABLE_STATUS
    RETRY_AFTER_SECONDS = RETRY_AFTER_SECONDS

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """دستگیرهٔ زنجیرهٔ میان‌افزار."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """اجرای زنجیرهٔ میان‌افزار (تبدیل خطا در ``process_exception`` انجام می‌شود).

        نکتهٔ ظریف و آموزنده: در جنگو، استثنای نما پیش از رسیدن به میان‌افزارهای
        بیرونی به پاسخِ ۵۰۰ تبدیل می‌شود (``convert_exception_to_response`` هر
        لایه را می‌پوشاند). پس برای دیدن آن، باید از هوک رسمی
        ``process_exception`` استفاده کرد، نه ``try/except`` دور زنجیره.
        """
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        """تبدیل خطای گذرای زیرساخت به ۵۰۳؛ در غیر این صورت ``None`` (⇒ ۵۰۰ معمول)."""
        if not is_transient_infrastructure_error(exception):
            return None
        logger.warning(
            "infrastructure_degraded",
            extra={
                "component": "database",
                "path": request.path,
                "error": str(exception)[:300],
                "request_id": get_current_request_id(),
            },
        )
        return self._unavailable_response(request)

    def _unavailable_response(self, request: HttpRequest) -> HttpResponse:
        """پاسخ ۵۰۳ مناسبِ نوع درخواست (HTML یا JSON)."""
        if request.path.startswith("/api/"):
            payload = {
                "type": "https://docs.mahdieh.ir/problems/service_unavailable",
                "title": "سرویس موقتاً در دسترس نیست",
                "status": self.SERVICE_UNAVAILABLE_STATUS,
                "code": "service_unavailable",
                "detail": service_unavailable_problem_detail(),
                "trace_id": get_current_request_id(),
            }
            response: HttpResponse = JsonResponse(
                payload,
                status=self.SERVICE_UNAVAILABLE_STATUS,
                content_type="application/problem+json",
            )
        else:
            response = render(
                request,
                "503.html",
                {
                    "request_id": get_current_request_id(),
                    # صفحهٔ ۵۰۳ استایل درون‌خطی دارد؛ چون CSP تولید اعمال‌شده است،
                    # nonce جنگو را پاس می‌دهیم تا استایل اضطراری هم اجرا شود.
                    "csp_nonce": getattr(request, "_csp_nonce", ""),
                },
                status=self.SERVICE_UNAVAILABLE_STATUS,
            )
        response["Retry-After"] = str(self.RETRY_AFTER_SECONDS)
        response["Cache-Control"] = "no-store"
        return response


class PersonalDataCacheMiddleware:
    """صفحات شخصی هرگز در کش مشترک یا تاریخچهٔ مرورگر نمانند.

    چرا لازم است؟ چون دادهٔ شخصی (رزروها، پروفایل، کد ملی، تابلوی کارکنان) اگر
    با هدر پیش‌فرض پاسخ بگیرد، می‌تواند در کش پروکسی میانی یا حافظهٔ دستگاه
    مشترک (مثلاً رایانهٔ کافی‌نت زائرسرا) بنشیند. پیام روشن HTTP این است:
    ``Cache-Control: private, no-store``.

    نکتهٔ ظریف: از ``setdefault`` استفاده می‌کنیم، نه انتساب؛ تا نماهایی که
    تصمیم دقیق‌تری گرفته‌اند (مثل ``/reservations/<code>/calendar.ics`` که خودش
    ``private, no-store`` می‌گذارد) بازنویسی نشوند.
    """

    #: مسیرهایی که همیشه دادهٔ شخصی هستند، حتی برای پاسخ‌های بی‌ورود (هدایت‌ها).
    PROTECTED_PREFIXES = (
        "/my/",
        "/profile/",
        "/board/",
        "/reservations/",
        "/book/",
        "/login/",
        "/certificate/",
        "/feedback/",
    )

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """دستگیرهٔ زنجیرهٔ میان‌افزار."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """افزودن هدر کشِ محافظه‌کارانه به پاسخ‌های شخصی."""
        response = self.get_response(request)
        user = getattr(request, "user", None)
        is_authenticated = bool(user is not None and getattr(user, "is_authenticated", False))
        if is_authenticated or request.path.startswith(self.PROTECTED_PREFIXES):
            response.setdefault("Cache-Control", "private, no-store")
            response.setdefault("Pragma", "no-cache")
        return response
