"""پیکربندی اپ وب — لایهٔ نمایش و محتوای راهنما.

این اپ «پوسته» است و هیچ منطق دامنه‌ای ندارد؛ کارش تبدیل سرویس‌ها و پرسمان‌های دامنه به
HTML/JSON تمیز است. تنها داده‌اش بازخورد بی‌نام «پاسخ مفید بود؟» راهنماست (``models.HelpVote``).
"""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class WebConfig(AppConfig):
    """پیکربندی اپ لایهٔ نمایش."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.web"
    label = "web"
    verbose_name = _("وب")

    def ready(self) -> None:
        """بارگذاری فیلترهای قالب در زمان راه‌اندازی — جای دیگری لازم نیست."""
        from apps.web import templatetags  # noqa: F401  (ثبت فیلترها)
