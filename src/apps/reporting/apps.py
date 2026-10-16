"""پیکربندی اپ گزارش‌گیری."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReportingConfig(AppConfig):
    """اپ گزارش‌گیری — خروجی اکسل، خلاصه‌های مدیریتی و حاکمیت خروجی."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reporting"
    verbose_name = _("گزارش‌گیری")
