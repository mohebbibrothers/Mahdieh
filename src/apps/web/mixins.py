"""میکسین‌های نما — کنترل دسترسی و رفتار مشترک صفحات وب."""

from __future__ import annotations

from typing import Any, cast

from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import View

from apps.identity.models import User


def current_user(request: HttpRequest) -> User:
    """کاربر واردشدهٔ درخواست — دسترسی پیش از رسیدن به این نقطه بررسی شده است.

    نکتهٔ نوع: ``request.user`` در استاب‌های جنگو ``User | AnonymousUser`` است؛
    این تابع آن را در یک نقطه باریک می‌کند تا نماها به ``is_staff_member`` و
    فیلدهای دامنه دسترسی نوع‌ایمن داشته باشند.
    """
    return cast("User", request.user)


class DashboardLoginMixin(View):
    """نیازمند ورود؛ کاربر مهمان به صفحهٔ ورود با ``next`` فرستاده می‌شود."""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """بررسی نشست پیش از رسیدن درخواست به نما."""
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("web:login"))
        return super().dispatch(request, *args, **kwargs)


class ProfileRequiredMixin(DashboardLoginMixin):
    """نیازمند ورود **و** پروفایل کامل (ثابت I5)."""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """کاربر وارد‌شدهٔ بدون پروفایل، به فرم پروفایل هدایت می‌شود."""
        if request.user.is_authenticated and not request.user.is_profile_complete:
            return redirect(f"{reverse('web:profile')}?next={request.get_full_path()}")
        return super().dispatch(request, *args, **kwargs)


class StaffRequiredMixin(DashboardLoginMixin):
    """نیازمند نقش کارکنان — در غیر این صورت پاسخ ۴۰۳ با لحن محترمانه."""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """بررسی عضویت در کارکنان پیش از رسیدن به نما."""
        if request.user.is_authenticated and not request.user.is_staff_member:
            return HttpResponse(
                "این بخش فقط برای کارکنان زائرسرا در دسترس است.",
                status=403,
                content_type="text/plain; charset=utf-8",
            )
        return super().dispatch(request, *args, **kwargs)
