"""پیکربندی اپ موجودی."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class InventoryConfig(AppConfig):
    """اپ موجودی — طبقات، واحدها، امکانات، مناسبت‌ها و بلوک‌ها."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    verbose_name = _("موجودی و واحدها")

    def ready(self) -> None:
        """اتصال سیگنال‌ها (همگام‌سازی تقویم پس از اصلاح ماه قمری)."""
        from apps.inventory import signals  # noqa: F401 - ثبت گیرنده‌ها
