"""نماهای ویژهٔ محیط توسعه — ورود نمایشی برای بازدید سریع سایت.

این ماژول **دقیقاً دو شرط** دارد و در تولید هیچ‌یک برقرار نیست:
    • ``DEBUG`` روشن باشد.
    • ``MAHDIEH_DEMO_MODE=true`` صریحاً در محیط تعیین شده باشد.
در غیر این صورت مسیر وجود ندارد (۴۰۴) و آزمون آن را تضمین می‌کند.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.generic import View

from apps.web.services import DEMO_ROLES, demo_mode_enabled, ensure_demo_user
from apps.web.views import SESSION_BACKEND, safe_next


class DemoLoginView(View):
    """ورود نمایشی با نقش دلخواه (زائر یا کارمند) — فقط برای محیط توسعه."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """باز کردن نشست نمایشی و هدایت به مقصد مناسب نقش."""
        role = str(kwargs.get("role", "guest"))
        if not demo_mode_enabled() or role not in DEMO_ROLES:
            raise Http404("ورود نمایشی در این محیط در دسترس نیست.")
        user = ensure_demo_user(role)
        auth_login(request, user, backend=SESSION_BACKEND)
        messages.success(
            request,
            f"نشست نمایشی «{user.display_name}» باز شد — این ورود فقط در محیط توسعه فعال است.",
        )
        destination = safe_next(request)
        if destination:
            return redirect(destination)
        return redirect("web:board" if user.is_staff_member else "web:my_reservations")
