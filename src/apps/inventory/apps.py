"""پیکربندی اپ موجودی."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

#: الگوی صفحهٔ هر واحد در بستهٔ فرانت (‎routes.json → dynamic‎).
UNIT_PAGE_PATTERN = "/units/[code]/"


class InventoryConfig(AppConfig):
    """اپ موجودی — طبقات، واحدها، امکانات، مناسبت‌ها و بلوک‌ها."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    verbose_name = _("موجودی و واحدها")

    def ready(self) -> None:
        """اتصال سیگنال‌ها و نگهبان صفحهٔ واحد فرانت (P-03)."""
        from apps.core.frontend import register_route_guard
        from apps.inventory import signals  # noqa: F401 - ثبت گیرنده‌ها
        from apps.inventory.selectors import unit_page_path

        register_route_guard(UNIT_PAGE_PATTERN, lambda params: unit_page_path(params["code"]))
