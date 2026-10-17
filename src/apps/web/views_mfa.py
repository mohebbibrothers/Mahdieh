"""صفحهٔ SSR تأیید دومرحله‌ای کارکنان — همتای وب ``/api/v1/auth/mfa/*``.

تا وقتی تابلوی کارکنان و پنل جنگو از همین سرور سرو می‌شوند، سد عامل دوم باید
یک صفحهٔ وب هم داشته باشد؛ وگرنه کارمند پشت سد گیر می‌کرد. منطق کاملاً در
``apps.identity.mfa`` است و این نما فقط فرم و پیام است.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import View

from apps.core.errors import DomainError
from apps.identity import mfa
from apps.web.mixins import DashboardLoginMixin, current_user
from apps.web.views import safe_next

TEMPLATE = "web/mfa.html"


class MfaView(DashboardLoginMixin, View):
    """ثبت اپ احراز هویت (بار اول) یا واردکردن کد (هر ورود/تأیید تازه)."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """نمایش فرم مناسب وضعیت کارمند."""
        user = current_user(request)
        if not user.is_staff_member:
            return redirect("web:home")
        status = mfa.status_for(request)
        context: dict[str, Any] = {"status": status, "next": safe_next(request)}
        if not status.enrolled:
            context["enrollment"] = mfa.begin_enrollment(user=user)
        return render(request, TEMPLATE, context)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """تأیید کد؛ در بار اول، نمایش یک‌بارهٔ کدهای بازیابی."""
        user = current_user(request)
        if not user.is_staff_member:
            return redirect("web:home")
        code = str(request.POST.get("code", ""))
        target = safe_next(request) or reverse("web:board")
        try:
            if mfa.status_for(request).enrolled:
                mfa.verify(request=request, user=user, code=code)
            else:
                codes = mfa.confirm_enrollment(request=request, user=user, code=code)
                return render(
                    request,
                    TEMPLATE,
                    {"status": mfa.status_for(request), "recovery_codes": codes, "next": target},
                )
        except DomainError as error:
            messages.error(request, error.detail)
            return redirect(f"{reverse('web:mfa')}?next={target}")
        messages.success(request, "تأیید دومرحله‌ای انجام شد.")
        return redirect(target)
