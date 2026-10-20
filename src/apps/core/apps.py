"""پیکربندی اپ هسته — پیکربندی لاگ و ثبت سیگنال‌ها در زمان آماده‌سازی."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    """هستهٔ مشترک: زیرساخت‌هایی که هیچ دامنه‌ای مالک آن‌ها نیست."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = _("هستهٔ مشترک")
    label = "core"

    def ready(self) -> None:
        """پیکربندی لاگ ساخت‌یافته و بارگذاری ماژول‌های جانبی."""
        from apps.core.logging import configure_logging

        configure_logging()
        # ثبت سیستم‌چک‌ها با import. درس آموخته (مرحلهٔ ۲): ``checks.py`` هیچ‌جا import
        # نمی‌شد ⇒ نگهبان‌های CORS/کوکی/منطقهٔ زمانی/Argon2 فقط در آزمون‌های مستقیم اجرا
        # می‌شدند، نه در ``check``/``production_check``. آزمون ثبت، بازگشت این خطا را می‌گیرد.
        from apps.core import checks, frontend  # noqa: F401
