"""پنل مدیریت دفتر تحویل پیامک — فقط خواندنی، برای عیب‌یابی پشتیبانی."""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.notifications.choices import SmsStatus
from apps.notifications.models import SmsDelivery


@admin.register(SmsDelivery)
class SmsDeliveryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """نمایش تحویل‌های پیامک؛ متن خام وجود ندارد (فقط نسخهٔ پوشانده)."""

    list_display = ("created_at", "template_key", "phone_masked", "status_badge", "provider")
    list_filter = ("template_key", "status", "provider")
    search_fields = ("phone_masked", "provider_ref", "dedup_key")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "template_key",
        "phone_masked",
        "body_redacted",
        "context",
        "status",
        "provider",
        "provider_ref",
        "error",
        "dedup_key",
        "attempts",
        "created_at",
        "sent_at",
    )

    def has_add_permission(self, request: object) -> bool:
        """افزودن دستی تحویل معنا ندارد — فقط سیستم ثبت می‌کند."""
        return False

    @admin.display(description=_("وضعیت"))
    def status_badge(self, obj: SmsDelivery) -> str:
        """نمایش رنگی وضعیت برای مرور سریع توسط پشتیبانی."""
        palette: dict[str, str] = {
            SmsStatus.SENT: "#0a7d33",
            SmsStatus.SUPPRESSED: "#8a6d00",
            SmsStatus.BLOCKED: "#8a4b00",
            SmsStatus.FAILED: "#a30000",
            SmsStatus.QUEUED: "#444",
        }
        color = palette.get(obj.status, "#444")
        return format_html('<b style="color:{}">{}</b>', color, obj.get_status_display())
