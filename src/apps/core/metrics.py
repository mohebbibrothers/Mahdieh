"""متریک‌های Prometheus — کسب‌وکار و فنی، هر دو.

قاعدهٔ پروژه (اصل ۷): رخداد مهم دامنه‌ای باید **متریک** داشته باشد، نه فقط
لاگ. مثال: «تصادم رزرو» نباید یک خط لاگ گم‌شده باشد، باید شمارنده باشد.
"""

from __future__ import annotations

from typing import Final

from django.http import HttpRequest, HttpResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# ─── متریک‌های فنی ────────────────────────────────────────────────────────────
HTTP_REQUESTS: Final = Counter(
    "mahdieh_http_requests_total",
    "تعداد درخواست‌های HTTP",
    ["method", "path_template", "status"],
)
HTTP_LATENCY: Final = Histogram(
    "mahdieh_http_request_duration_seconds",
    "توزیع زمان پاسخ درخواست",
    ["method", "path_template"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
DB_QUERIES: Final = Histogram(
    "mahdieh_db_query_count",
    "تعداد کوئری دیتابیس در هر درخواست (کشف N+1)",
    ["path_template"],
    buckets=(0, 1, 3, 5, 10, 25, 50, 100),
)
CACHE_EVENTS: Final = Counter(
    "mahdieh_cache_events_total",
    "رخدادهای کش",
    ["operation", "result"],
)

# ─── متریک‌های دامنه (کسب‌وکار) ───────────────────────────────────────────────
RESERVATION_EVENTS: Final = Counter(
    "mahdieh_reservation_events_total",
    "رخدادهای چرخهٔ عمر رزرو",
    ["event"],  # created, confirmed, cancelled, superseded, no_show, conflict
)
RESERVATION_CONFLICTS: Final = Counter(
    "mahdieh_reservation_conflicts_total",
    "تصادم‌های همزمانی روی یک سانس (قید دیتابیس فعال شد)",
    ["unit_code"],
)
OUTBOX_LAG: Final = Gauge(
    "mahdieh_outbox_pending",
    "تعداد رخدادهای منتشرنشده در Outbox",
)
OUTBOX_DEAD: Final = Gauge(
    "mahdieh_outbox_dead",
    "تعداد رخدادهای راکد (نیازمند مداخلهٔ عملیاتی)",
)
SMS_SENT: Final = Counter(
    "mahdieh_sms_total",
    "پیامک‌های ارسالی",
    ["provider", "result"],
)
POLICY_READS: Final = Counter(
    "mahdieh_policy_reads_total",
    "خواندن سیاست‌ها",
    ["key", "source"],
)


def metrics_view(request: HttpRequest) -> HttpResponse:
    """نمای ``/metrics`` — فقط برای کارکنان/پایش داخلی.

    دسترسی: فقط کاربر کارکنان یا آی‌پی‌های داخلی؛ در غیر این صورت ۴۰۴
    (۳۰۴ برای پنهان‌کردن وجود مسیر استفاده نمی‌کنیم تا گیج‌کننده نباشد).
    """
    user = getattr(request, "user", None)
    if not (user is not None and user.is_authenticated and user.is_staff):
        return HttpResponse(status=404)

    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


def track_http_response(
    *, request: HttpRequest, response: HttpResponse, route: str, duration: float
) -> None:
    """ثبت متریک یک درخواست — از میان‌افزار فراخوانی می‌شود."""
    HTTP_REQUESTS.labels(
        method=request.method or "GET",
        path_template=route,
        status=str(response.status_code),
    ).inc()
    HTTP_LATENCY.labels(method=request.method or "GET", path_template=route).observe(duration)
