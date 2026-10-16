"""قالب‌بندی خطا بر پایهٔ RFC 9457 و دستگیرهٔ استثناهای DRF.

تصمیم ADR-0006: هیچ پوشش سفارشی ``{success, data}`` نداریم.
خطا ⇒ ``application/problem+json`` و موفقیت ⇒ بدنهٔ خالص منبع.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.errors import DomainError
from apps.core.middleware import get_current_request_id, get_current_trace_id

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_BASE_URI = "https://docs.mahdieh.ir/problems"


def build_problem(
    *,
    code: str,
    title: str,
    status: int,
    detail: str,
    instance: str | None = None,
    errors: dict[str, Any] | None = None,
    **extensions: Any,
) -> dict[str, Any]:
    """ساخت بدنهٔ Problem Details با فیلدهای استاندارد و افزونه‌های دامنه‌ای."""
    problem: dict[str, Any] = {
        "type": f"{PROBLEM_BASE_URI}/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    request_id = get_current_request_id()
    if instance:
        problem["instance"] = instance
    if request_id:
        problem["trace_id"] = request_id
    trace_id = get_current_trace_id()
    if trace_id:
        problem["otel_trace_id"] = trace_id
    if errors:
        problem["errors"] = errors
    problem.update(extensions)
    return problem


# نگاشت استثناهای DRF به شناسهٔ خطای پروژه
_DRF_CODE_MAP: dict[type[Exception], tuple[str, str]] = {
    drf_exceptions.NotAuthenticated: ("authentication_required", "ورود لازم است"),
    drf_exceptions.AuthenticationFailed: ("authentication_failed", "احراز هویت ناموفق"),
    drf_exceptions.PermissionDenied: ("permission_denied", "دسترسی مجاز نیست"),
    drf_exceptions.NotFound: ("not_found", "یافت نشد"),
    drf_exceptions.MethodNotAllowed: ("method_not_allowed", "این روش مجاز نیست"),
    drf_exceptions.Throttled: ("rate_limited", "تعداد درخواست زیاد"),
    drf_exceptions.UnsupportedMediaType: ("unsupported_media_type", "نوع محتوا پشتیبانی نمی‌شود"),
    drf_exceptions.ParseError: ("malformed_payload", "بدنهٔ درخواست معتبر نیست"),
    drf_exceptions.ValidationError: ("validation_error", "ورودی نامعتبر"),
}


def problem_details_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """دستگیرهٔ استثنا برای DRF — همه‌چیز را به RFC 9457 تبدیل می‌کند.

    نکتهٔ مهم امنیتی: هیچ‌گاه ``str(exc)`` خام برای خطاهای ناشناخته به کاربر
    نمی‌رود؛ پیام داخلی فقط در لاگ می‌ماند و کاربر شناسهٔ ردیابی می‌گیرد.
    """
    request = context.get("request")
    instance = request.path if request is not None else None

    # ۱) خطاهای دامنه — مستقیم و بدون واسطه.
    # نکته: شناسهٔ ردیابی باید همین‌جا تزریق شود؛ وگرنه کاربر برای پیگیری
    # پشتیبانی چیزی در دست ندارد (باگ واقعی که تست قرارداد آن را گرفت).
    if isinstance(exc, DomainError):
        problem = exc.as_problem(instance=instance, trace_id=get_current_request_id())
        return Response(problem, status=exc.status, content_type=PROBLEM_CONTENT_TYPE)

    # ۲) استثناهای DRF
    response = drf_exception_handler(exc, context)
    if response is not None:
        code, title = _DRF_CODE_MAP.get(type(exc), ("request_error", "خطای درخواست"))
        detail = _humanize_detail(exc, response.data)
        errors = (
            response.data if isinstance(response.data, dict) and len(response.data) > 1 else None
        )
        # قرارداد پروژه: «ورود لازم است» همیشه ۴۰۱ است — نه ۴۰۳.
        # DRF با احراز هویت کوکی، برای نبود WWW-Authenticate به ۴۰۳ سقوط می‌کند؛
        # فرانت ما بر پایهٔ کد وضعیت تصمیم می‌گیرد (نمایش فرم ورود یا صفحهٔ ۴۰۳)،
        # پس کد را صریح می‌کنیم تا رفتار API قابل پیش‌بینی بماند.
        if isinstance(exc, drf_exceptions.NotAuthenticated):
            response.status_code = 401

        extensions: dict[str, Any] = {}
        if isinstance(exc, drf_exceptions.Throttled):
            wait = getattr(exc, "wait", None)
            if wait is not None and hasattr(wait, "total_seconds"):
                extensions["retry_after"] = float(wait.total_seconds())

        payload = build_problem(
            code=code,
            title=title,
            status=response.status_code,
            detail=detail,
            instance=instance,
            errors=errors,
            **extensions,
        )
        response.data = payload
        response.content_type = PROBLEM_CONTENT_TYPE
        return response

    # ۳) خطای پیش‌بینی‌نشده — لاگ کامل، پاسخ کمینه
    logger.error(
        "unhandled_exception",
        extra={"path": instance, "exc_type": type(exc).__name__},
        exc_info=exc,
    )
    return Response(
        build_problem(
            code="internal_error",
            title=str(_("خطای داخلی سرور")),
            status=500,
            detail=str(_("مشکلی در پردازش رخ داد. شناسهٔ ردیابی را به پشتیبانی بدهید.")),
            instance=instance,
        ),
        status=500,
        content_type=PROBLEM_CONTENT_TYPE,
    )


def _humanize_detail(exc: Exception, data: Any) -> str:
    """ساخت پیام فارسی خوانا از دادهٔ خطای DRF."""
    if isinstance(exc, drf_exceptions.Throttled):
        return str(_("تعداد درخواست‌های شما زیاد است. کمی صبر کنید."))
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    if isinstance(data, dict):
        flat: list[str] = []
        for field, messages in data.items():
            if isinstance(messages, list):
                flat.extend(f"{field}: {msg}" for msg in messages)
            else:
                flat.append(f"{field}: {messages}")
        if flat:
            return " · ".join(flat[:5])
    return str(_("درخواست معتبر نیست."))


def problem_response(
    *,
    code: str,
    title: str,
    status: int,
    detail: str,
    instance: str | None = None,
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JsonResponse:
    """پاسخ Problem Details برای نماهای غیر-DRF (میان‌افزار، صفحات خطا)."""
    return JsonResponse(
        build_problem(
            code=code, title=title, status=status, detail=detail, instance=instance, **extensions
        ),
        status=status,
        content_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )
