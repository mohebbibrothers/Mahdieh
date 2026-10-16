"""پیکربندی اپ تحلیل‌ها — «چشم مدیریتی» زائرسرا.

این اپ نه مدل دارد و نه مهاجرت؛ فقط رخدادهای موجود (رزرو، لغو، عدم‌حضور،
تقاضا) را می‌خواند و به شاخص و نمودار تبدیل می‌کند. جدا نگه‌داشتنش سه سود دارد:
دامنه سبک می‌ماند، هزینهٔ محاسبه در یک نقطه جمع می‌شود، و می‌توان کل اپ را در
محیط‌های سبک (تست) بی‌دردسر خاموش یا جایگزین کرد.
"""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AnalyticsConfig(AppConfig):
    """اپ تحلیل — شاخص‌های اشغال، کیفیت مهمان و پیش‌بینی تقاضا."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
    verbose_name = _("تحلیل‌ها")
