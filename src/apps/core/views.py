"""نماهای هسته — صفحهٔ خانه، فراداده، robots و نمای خطای سفارشی."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from apps.core.jalali import format_jalali

# ساختار فیزیکی زائرسرا — در فاز ۲ از دیتابیس خوانده می‌شود؛
# در فاز ۰ همان داده به‌صورت صریح نمایش داده می‌شود (بدون هیچ ادعای نادرست).
FLOORS: tuple[dict[str, Any], ...] = (
    {
        "number": 1,
        "title": "طبقهٔ اول",
        "units": ["سوئیت شمارهٔ ۱", "اتاق بزرگ شمارهٔ ۱"],
    },
    {
        "number": 2,
        "title": "طبقهٔ دوم",
        "units": ["سوئیت شمارهٔ ۲", "اتاق بزرگ شمارهٔ ۲"],
    },
    {
        "number": 3,
        "title": "طبقهٔ سوم",
        "units": ["اتاق بزرگ شمارهٔ ۳"],
    },
)

CAPABILITIES: tuple[dict[str, str], ...] = (
    {
        "icon": "⚡",
        "title": "رزرو در کمتر از یک دقیقه",
        "body": "ثبت‌نام فقط با شمارهٔ تلفن؛ بدون ایمیل، بدون رمز، بدون فرم‌های طولانی.",
    },
    {
        "icon": "🛡",
        "title": "دوباره‌رزروی ناممکن",
        "body": "تضمین در سطح دیتابیس؛ حتی اگر هزار نفر هم‌زمان درخواست بدهند.",
    },
    {
        "icon": "🗓",
        "title": "تقویم جلالی هوشمند",
        "body": "تعطیلات، پنجشنبه و جمعه و ایام پیک را در یک نگاه ببینید.",
    },
    {
        "icon": "🔔",
        "title": "خبر شدن از سانس آزادشده",
        "body": "اگر بازهٔ مورد نظرتان پُر بود، در صف می‌مانید و با اولین لغو خبر می‌شوید.",
    },
    {
        "icon": "🧾",
        "title": "گواهی اقامت با کد QR",
        "body": "پذیرش با یک اسکن، مهمان را تأیید می‌کند.",
    },
    {
        "icon": "🆓",
        "title": "کاملاً رایگان",
        "body": "بدون درگاه پرداخت، بدون ودیعه، بدون هزینهٔ پنهان.",
    },
)

ENGINEERING_FACTS: tuple[dict[str, str], ...] = (
    {"value": "۵", "label": "واحد اقامتی"},
    {"value": "۱۴", "label": "نیم‌روز حداکثر اقامت"},
    {"value": "۰", "label": "دوباره‌رزروی ممکن"},
    {"value": "۱۰۰٪", "label": "عملیات مدیریتی دارای سابقهٔ حسابرسی"},
)


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """صفحهٔ خانه — معرفی زائرسرا و وضعیت ساخت سامانه."""
    today = timezone.localdate()
    context: dict[str, Any] = {
        "floors": FLOORS,
        "capabilities": CAPABILITIES,
        "facts": ENGINEERING_FACTS,
        "today": today,
        "today_jalali": format_jalali(today, with_weekday=True),
        "subtitle": "سامانهٔ رزرو و مدیریت اقامت — در حال ساخت",
        "phase": "فاز ۰ — بنیان و انضباط",
    }
    return render(request, "core/home.html", context)


@require_GET
@cache_page(60 * 15)
def robots(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
    """robots.txt — مسیرهای خصوصی فهرست نمی‌شوند."""
    lines = [
        "User-agent: *",
        "Allow: /$",
        "Allow: /units/",
        "Allow: /calendar/",
        "Disallow: /api/",
        "Disallow: /health/",
        "Disallow: /django-admin/",
        "Disallow: /me/",
        f"Sitemap: {settings.SITE_URL}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@require_GET
def meta(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    """فرادادهٔ سیستم — برای پایش و بررسی نسخه توسط تیم پشتیبانی."""
    return JsonResponse(
        {
            "service": settings.OTEL_SERVICE_NAME,
            "environment": settings.ENVIRONMENT,
            "schema_version": "v1",
            "today_jalali": format_jalali(timezone.localdate(), numeric=True),
            "timezone": settings.TIME_ZONE,
        }
    )


def server_error(request: HttpRequest, template_name: str = "500.html") -> HttpResponse:
    """صفحهٔ خطای ۵۰۰ با شناسهٔ ردیابی — برای پیگیری توسط پشتیبانی."""
    return render(
        request,
        template_name,
        {"request_id": getattr(request, "request_id", "")},
        status=500,
    )


def not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """صفحهٔ ۴۰۴ سازگار با لحن سایت.

    امضای این تابع باید مطابق قرارداد جنگو بماند (``handler404``).
    """
    del exception
    return render(
        request,
        "404.html",
        {"request_id": getattr(request, "request_id", "")},
        status=404,
    )
