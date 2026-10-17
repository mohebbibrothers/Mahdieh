"""میان‌افزار امنیت نشست — سد عامل دوم کارکنان + «آخرین فعالیت» نشست‌ها.

چرا میان‌افزار و نه فقط مجوز DRF؟ اختیار کارمندی در چند لایه پخش است: نماهای
API، صفحه‌های SSR تابلو، پنل جنگو و حتی مسیرهای مشترک زائر (جزئیات/لغو رزرو).
یک سد در ورودی، «همه‌جا» را پوشش می‌دهد و هیچ نمای تازه‌ای نمی‌تواند فراموشش
کند. قاعده ساده است: **نشست کارمندِ تأییدنشده فقط به مسیرهای ورود و ثبت عامل
دوم دسترسی دارد.**
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final
from urllib.parse import quote

from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.problems import problem_response
from apps.identity import mfa, sessions

API_PREFIX: Final[str] = "/api/"
#: مسیرهای API که پیش از تأیید عامل دوم لازم‌اند (ورود، خروج، CSRF، ثبت/تأیید عامل دوم)
#: و مسیرهای عمومی توکن‌محور که اصلاً به نشست تکیه ندارند (گواهی، نظرسنجی).
API_ALLOWED_PREFIXES: Final[tuple[str, ...]] = (
    "/api/v1/auth/",
    "/api/v1/certificates/",
    "/api/v1/feedback/",
)
API_ALLOWED_EXACT: Final[frozenset[str]] = frozenset(
    {
        "/api/v1/me",
        "/api/v1/config",
        "/api/v1/help",
        "/api/v1/meta",
        "/api/schema/",
        "/api/docs/",
    }
)
#: مسیرهای غیر-API مجاز پیش از تأیید (دارایی‌ها، سلامت، ورود/خروج، صفحهٔ عامل دوم).
WEB_ALLOWED_PREFIXES: Final[tuple[str, ...]] = (
    "/static/",
    "/media/",
    "/health/",
    "/csp-report/",
    "/metrics",
    "/login/",
    "/logout/",
    "/board/mfa/",
    "/django-admin/logout/",
)


class StaffSecurityMiddleware:
    """سد عامل دوم کارکنان و به‌روزرسانی کم‌هزینهٔ «آخرین فعالیت» نشست."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        """نگه‌داشت زنجیرهٔ میان‌افزارها."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """بررسی سد پیش از نما و ثبت فعالیت نشست."""
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            blocked = self._gate(request)
            if blocked is not None:
                return blocked
            sessions.touch(request)
        return self.get_response(request)

    @staticmethod
    def _gate(request: HttpRequest) -> HttpResponseBase | None:
        if mfa.is_satisfied(request):
            return None
        path = request.path
        if path.startswith(API_PREFIX):
            if path in API_ALLOWED_EXACT or path.startswith(API_ALLOWED_PREFIXES):
                return None
            error = mfa.mfa_required_error(request)
            return problem_response(
                code=error.code,
                title=error.title,
                status=error.status,
                detail=error.detail,
                instance=path,
                **error.extensions,
            )
        if path.startswith(WEB_ALLOWED_PREFIXES):
            return None
        return _redirect_to_mfa(request)


def _redirect_to_mfa(request: HttpRequest) -> HttpResponse:
    """هدایت صفحه‌های SSR/پنل به صفحهٔ عامل دوم با بازگشت به همان صفحه."""
    target = reverse("web:mfa")
    if request.method == "GET":
        target = f"{target}?next={quote(request.get_full_path(), safe='/')}"
    return redirect(target)
