"""پنل «بازخوردهای راهنما» — کدام پاسخ‌ها به زائر کمک نکرده‌اند؟ (فقط مشاهده)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse

from apps.web import help_content
from apps.web.models import HelpVote


def vote_summary(queryset: Any) -> list[dict[str, Any]]:
    """جمع رأی هر پرسش (نامفیدترین بالا) — همان صافی‌های فهرست پنل اعمال می‌شود."""
    rows = (
        queryset.order_by()
        .values("slug")
        .annotate(yes=Count("pk", filter=Q(helpful=True)), no=Count("pk", filter=Q(helpful=False)))
    )
    summary: list[dict[str, Any]] = []
    for row in rows:
        item = help_content.item_by_slug(row["slug"])
        total = row["yes"] + row["no"]
        summary.append(
            {
                "slug": row["slug"],
                "question": item.question if item else row["slug"],
                "yes": row["yes"],
                "no": row["no"],
                "total": total,
                "ratio": round(100 * row["yes"] / total) if total else 0,
            }
        )
    summary.sort(key=lambda entry: (entry["ratio"], -entry["total"]))
    return summary


@admin.register(HelpVote)
class HelpVoteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """رأی‌های بی‌نام راهنما با جدول خلاصه — برای بهترکردن متن پاسخ‌ها."""

    list_display = ("question", "helpful", "created_at")
    list_filter = ("helpful", "slug", "created_at")
    date_hierarchy = "created_at"
    readonly_fields = ("slug", "helpful", "created_at")

    @admin.display(description="پرسش", ordering="slug")
    def question(self, obj: HelpVote) -> str:
        """متن پرسش به‌جای نشانهٔ لاتین."""
        item = help_content.item_by_slug(obj.slug)
        return item.question if item else obj.slug

    def has_add_permission(self, request: HttpRequest) -> bool:
        """رأی فقط از راهنمای سایت می‌آید."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """رأی تغییرناپذیر است (حذف برای پاک‌سازی آزمایشی مجاز می‌ماند)."""
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """فهرست + جدول خلاصهٔ هر پرسش با همان صافی‌های فعال."""
        response = super().changelist_view(request, extra_context)
        context = getattr(response, "context_data", None)
        if context and "cl" in context:
            context["vote_summary"] = vote_summary(context["cl"].queryset)
        return response
