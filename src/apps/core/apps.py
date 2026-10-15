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
