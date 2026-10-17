"""API میز راهنما — همان محتوای صفحهٔ SSR، برای فرانت مستقل.

اقدام‌ها (``action``) به‌جای نشانی سخت، یک «هدف معنایی» دارند (``target``، مثل
``calendar`` یا ``my_reservations``)؛ فرانت آن را به مسیر خودش نگاشت می‌کند تا
تغییر مسیرهای فرانت، محتوای راهنما را نشکند.
"""

from __future__ import annotations

from typing import Any, Final

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.web import help_content

MAX_SEARCH_RESULTS: Final[int] = 8
HELP_CACHE_CONTROL: Final[str] = "public, max-age=120, stale-while-revalidate=600"


def _target(url_name: str) -> str:
    """هدف معنایی از نام مسیر SSR (``web:calendar`` ⇒ ``calendar``)."""
    return url_name.split(":", 1)[-1]


def _item(item: help_content.HelpItem, values: dict[str, str]) -> dict[str, Any]:
    """یک پرسش با متن پرشده از سیاست‌های زنده."""
    return {
        "slug": item.slug,
        "question": item.question,
        "answer": help_content.render(item.answer, values),
        "steps": [help_content.render(step, values) for step in item.steps],
        "tags": list(item.tags),
        "action": (
            {"label": item.action.label, "target": _target(item.action.url_name)}
            if item.action
            else None
        ),
        "related": [related.slug for related in help_content.related_items(item)],
    }


class HelpSerializer(serializers.Serializer[Any]):
    """پاسخ میز راهنما (سند)."""

    query = serializers.CharField(allow_blank=True)
    matches = serializers.ListField(child=serializers.DictField())
    categories = serializers.ListField(child=serializers.DictField())
    quick_steps = serializers.ListField(child=serializers.DictField())
    contact = serializers.DictField(child=serializers.CharField())
    note = serializers.CharField()


class HelpView(APIView):
    """میز راهنما: دسته‌ها، پرسش‌ها و جست‌وجوی فارسی (اعداد از سیاست زنده)."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["راهنما"],
        summary="میز راهنما",
        parameters=[
            OpenApiParameter("q", str, description="جست‌وجوی فارسی (با تحمل نیم‌فاصله و ی/ک)"),
            OpenApiParameter("cat", str, description="فقط یک دسته (slug)"),
        ],
        responses={200: HelpSerializer},
    )
    def get(self, request: Request) -> Response:
        """محتوای راهنما با نتیجهٔ جست‌وجو (در صورت وجود پرسش)."""
        values = help_content.policy_values()
        query = str(request.query_params.get("q") or "").strip()[:80]
        active = str(request.query_params.get("cat") or "").strip()
        matches = help_content.search(query, values=values, limit=MAX_SEARCH_RESULTS)
        response = Response(
            {
                "query": query,
                "matches": [
                    {
                        "item": _item(match.item, values),
                        "category": match.category.slug,
                        "score": match.score,
                        "snippet": match.snippet,
                    }
                    for match in matches
                ],
                "categories": [
                    {
                        "slug": category.slug,
                        "title": category.title,
                        "icon": category.icon,
                        "summary": category.summary,
                        "items": [_item(item, values) for item in category.items],
                    }
                    for category in help_content.CATEGORIES
                    if not active or category.slug == active
                ],
                "quick_steps": [
                    {
                        "icon": step["icon"],
                        "title": step["title"],
                        "text": step["text"],
                        "target": _target(step["url_name"]),
                    }
                    for step in help_content.QUICK_STEPS
                ],
                "contact": help_content.contact(),
                "note": help_content.SITE_HELP_NOTE,
            }
        )
        response["Cache-Control"] = HELP_CACHE_CONTROL
        return response
