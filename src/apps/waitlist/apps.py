"""پیکربندی اپ صف انتظار."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class WaitlistConfig(AppConfig):
    """اپ صف انتظار — درخواست‌ها، پیشنهادهای انحصاری و آبشار عدالت."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.waitlist"
    verbose_name = _("صف انتظار")

    def ready(self) -> None:
        """ثبت مشترک‌های رخداد در زمان راه‌اندازی اپ.

        ثبت این‌جا (و نه در import ماژول‌ها) تضمین می‌کند مشترک‌ها فقط یک‌بار و
        پس از آماده‌شدن رجیستری اپ‌ها فعال شوند.
        """
        from apps.waitlist import handlers  # وابستگی زمان راه‌اندازی

        handlers.subscribe_all()
