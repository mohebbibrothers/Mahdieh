"""پیکربندی اپ پیامک."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    """اپ اعلان و پیامک — پورت ارائه‌دهنده، قالب‌ها و دفتر تحویل."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    verbose_name = _("اعلان‌ها و پیامک")
