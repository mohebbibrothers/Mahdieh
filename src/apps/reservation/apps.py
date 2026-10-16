"""پیکربندی اپ رزرو."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReservationConfig(AppConfig):
    """اپ رزرو — مدل رزرو، سیاست‌ها، موجودی محاسبه‌شده و کارهای زمان‌بندی‌شده."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reservation"
    verbose_name = _("رزرو و تخصیص")
