"""پیکربندی اپ هویت."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class IdentityConfig(AppConfig):
    """هویت و دسترسی — تنها مالک مدل کاربر."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identity"
    verbose_name = _("هویت و دسترسی")
    label = "identity"
