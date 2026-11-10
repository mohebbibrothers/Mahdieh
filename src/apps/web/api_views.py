"""API محتوای سایت برای فرانت مستقل: میز راهنما، بازخورد پاسخ‌ها و اوقات شرعی.

اقدام‌ها (``action``) به‌جای نشانی سخت، یک «هدف معنایی» دارند (``target``، مثل
``calendar`` یا ``my_reservations``)؛ فرانت آن را به مسیر خودش نگاشت می‌کند تا
تغییر مسیرهای فرانت، محتوای راهنما را نشکند.

همهٔ پاسخ‌ها سریالایزر نوع‌دار دارند تا قرارداد OpenAPI (و نوع‌های تولیدی فرانت) دقیق بماند.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import prayer_times
from apps.core.jalali import format_jalali
from apps.identity.throttles import ScopedThrottle
from apps.inventory import official_calendar
from apps.inventory.selectors import lunar_month_starts
from apps.web import help_content
from apps.web.models import HelpVote

MAX_SEARCH_RESULTS: Final[int] = 8
HELP_CACHE_CONTROL: Final[str] = "public, max-age=120, stale-while-revalidate=600"
PRAYER_CACHE_CONTROL: Final[str] = "public, max-age=1800, stale-while-revalidate=3600"
PRAYER_DEFAULT_DAYS: Final[int] = 7
PRAYER_MAX_DAYS: Final[int] = 14

PRAYER_NOTE: Final[str] = (
    "محاسبهٔ نجومی به افق حرم مطهر؛ ممکن است با اعلام رسمی تا یک دقیقه تفاوت داشته باشد."
)
# محرم (۱) و صفر (۲): نقاره‌خانهٔ حرم رضوی به احترام ایام عزاداری خاموش است.
NAQQAREH_SILENT_MONTHS: Final[frozenset[int]] = frozenset({1, 2})
NAQQAREH_TITLE: Final[str] = "آیین نقاره‌خانهٔ حرم"
NAQQAREH_TEXT: Final[str] = (
    "نقاره‌خانهٔ حرم مطهر هر روز دو بار نواخته می‌شود: صبح، پیش از طلوع آفتاب، و عصر، پیش از "
    "غروب آفتاب — یادآور اینکه فرصت نماز رو به پایان است. شب‌های میلاد معصومان و عیدهای "
    "مذهبی هم نقاره‌زنی برپاست و در دو ماه محرم و صفر، به احترام عزاداری، نواخته نمی‌شود."
)


def _lunar(day: date, starts: Mapping[tuple[int, int], date]) -> dict[str, Any]:
    """تاریخ قمری رسمی یک روز و نواخته‌شدن نقاره در آن (محرم و صفر: سکوت نقاره‌خانه)."""
    year, month, mday = official_calendar.hijri_of(day, starts)
    return {
        "hijri": official_calendar.hijri_label(year, month, mday),
        "naqqareh": month not in NAQQAREH_SILENT_MONTHS,
    }


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


# ─── سریالایزرهای سند (فقط خروجی) ───────────────────────────────────────────
class HelpActionSerializer(serializers.Serializer[Any]):
    """دکمهٔ اقدام پایان پاسخ: برچسب + هدف معنایی."""

    label = serializers.CharField()  # type: ignore[assignment]
    target = serializers.CharField()


class HelpItemSerializer(serializers.Serializer[Any]):
    """یک پرسش راهنما (متن‌ها پرشده از سیاست زنده)."""

    slug = serializers.CharField()
    question = serializers.CharField()
    answer = serializers.CharField()
    steps = serializers.ListField(child=serializers.CharField())
    tags = serializers.ListField(child=serializers.CharField())
    action = HelpActionSerializer(allow_null=True)
    related = serializers.ListField(child=serializers.CharField())


class HelpCategorySerializer(serializers.Serializer[Any]):
    """یک دستهٔ راهنما با پرسش‌هایش."""

    slug = serializers.CharField()
    title = serializers.CharField()
    icon = serializers.CharField()
    summary = serializers.CharField()
    items = HelpItemSerializer(many=True)


class HelpMatchSerializer(serializers.Serializer[Any]):
    """یک نتیجهٔ جست‌وجو با امتیاز و برش متن."""

    item = HelpItemSerializer()
    category = serializers.CharField()
    score = serializers.IntegerField()
    snippet = serializers.CharField()


class HelpQuickStepSerializer(serializers.Serializer[Any]):
    """یک گام «رزرو در چهار حرکت» (نشان از سیاست زنده)."""

    icon = serializers.CharField()
    title = serializers.CharField()
    text = serializers.CharField()
    target = serializers.CharField()
    badge = serializers.CharField()


class HelpRuleSerializer(serializers.Serializer[Any]):
    """یک ردیف جدول «قواعد اقامت» (زنده از سامانه)."""

    key = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]
    value = serializers.CharField()


class HelpChecklistSerializer(serializers.Serializer[Any]):
    """یک گام چک‌لیست میز پذیرش (برگ چاپی)."""

    title = serializers.CharField()
    text = serializers.CharField()


class HelpContactSerializer(serializers.Serializer[Any]):
    """تماس پذیرش — رشتهٔ خالی یعنی «تنظیم نشده» (هرگز شمارهٔ ساختگی)."""

    phone = serializers.CharField(allow_blank=True)
    phone_display = serializers.CharField(allow_blank=True)
    address = serializers.CharField(allow_blank=True)


class HelpStatsSerializer(serializers.Serializer[Any]):
    """شمار خلاصهٔ راهنما."""

    categories = serializers.IntegerField()
    questions = serializers.IntegerField()
    steps = serializers.IntegerField()
    contact_ready = serializers.BooleanField()


class HelpSerializer(serializers.Serializer[Any]):
    """پاسخ میز راهنما (سند)."""

    query = serializers.CharField(allow_blank=True)
    matches = HelpMatchSerializer(many=True)
    categories = HelpCategorySerializer(many=True)
    quick_steps = HelpQuickStepSerializer(many=True)
    rules = HelpRuleSerializer(many=True)
    staff_checklist = HelpChecklistSerializer(many=True)
    values = serializers.DictField(child=serializers.CharField())
    stats = HelpStatsSerializer()
    faq = serializers.ListField(
        child=serializers.CharField(), help_text="نشانهٔ پرسش‌های داده‌ی ساخت‌یافتهٔ FAQPage"
    )
    today = serializers.CharField(help_text="تاریخ امروز (جلالی، با روز هفته) — نشانهٔ برگ چاپی")
    contact = HelpContactSerializer()
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
        stats = help_content.stats(values)
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
                        "badge": step["badge"],
                    }
                    for step in help_content.quick_steps(values)
                ],
                "rules": help_content.rules_table(values),
                "staff_checklist": list(help_content.STAFF_CHECKLIST),
                "values": values,
                "stats": {
                    key: stats[key] for key in ("categories", "questions", "steps", "contact_ready")
                },
                "faq": list(help_content.FAQ_SLUGS),
                "today": help_content.today_label(),
                "contact": help_content.contact(),
                "note": help_content.SITE_HELP_NOTE,
            }
        )
        response["Cache-Control"] = HELP_CACHE_CONTROL
        return response


# ─── بازخورد «پاسخ مفید بود؟» ───────────────────────────────────────────────
class HelpFeedbackSerializer(serializers.Serializer[Any]):
    """رأی بی‌نام دربارهٔ یک پاسخ."""

    slug = serializers.ChoiceField(choices=[item.slug for item in help_content.all_items()])
    helpful = serializers.BooleanField()


class HelpFeedbackThrottle(ScopedThrottle):
    """سقف رأی هر آی‌پی (نبود نرخ در تنظیمات ⇒ خاموش، مثل باقی سقف‌های دامنه)."""

    scope = "help_feedback"


class HelpFeedbackView(APIView):
    """ثبت بی‌نام «مفید بود / نبود» — بی‌شناسهٔ فرد، با سقف درخواست."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_classes = [HelpFeedbackThrottle]

    @extend_schema(
        tags=["راهنما"],
        summary="بازخورد یک پاسخ راهنما",
        request=HelpFeedbackSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        """یک رأی؛ نشانهٔ ناشناخته ⇒ ۴۲۲ (رأی بی‌صاحب ذخیره نمی‌شود)."""
        serializer = HelpFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        HelpVote.objects.create(
            slug=serializer.validated_data["slug"],
            helpful=serializer.validated_data["helpful"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── اوقات شرعی ─────────────────────────────────────────────────────────────
class PrayerSlotSerializer(serializers.Serializer[Any]):
    """یک وقت شرعی."""

    key = serializers.ChoiceField(choices=[key for key, _ in prayer_times.SLOTS])
    label = serializers.CharField()  # type: ignore[assignment]
    at = serializers.DateTimeField(help_text="زمان با منطقهٔ تهران (گردشده به دقیقه)")


class PrayerDaySerializer(serializers.Serializer[Any]):
    """اوقات یک روز."""

    date = serializers.DateField()
    label = serializers.CharField(help_text="تاریخ جلالی با روز هفته")  # type: ignore[assignment]
    hijri = serializers.CharField(help_text="تاریخ قمری بر پایهٔ تقویم رسمی ایران، مثل «۳ رجب ۱۴۴۸»")
    naqqareh = serializers.BooleanField(
        help_text="نقاره‌خانهٔ حرم در این روز نواخته می‌شود؟ (در محرم و صفر، به احترام عزاداری، نه)"
    )
    slots = PrayerSlotSerializer(many=True)


class PrayerRitualSerializer(serializers.Serializer[Any]):
    """یک آیین حرم وابسته به اوقات (مثل نقاره‌خانه)."""

    title = serializers.CharField()
    text = serializers.CharField()


class PrayerTimesSerializer(serializers.Serializer[Any]):
    """اوقات شرعی چند روز پیاپی به افق مشهد."""

    city = serializers.CharField()
    method = serializers.CharField()
    note = serializers.CharField()
    days = PrayerDaySerializer(many=True)
    naqqareh = PrayerRitualSerializer()


class PrayerTimesView(APIView):
    """اوقات شرعی به افق حرم مطهر — از امروز، برای چند روز."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["راهنما"],
        summary="اوقات شرعی به افق مشهد",
        parameters=[
            OpenApiParameter(
                "days",
                int,
                description=f"شمار روزها از امروز (۱ تا {PRAYER_MAX_DAYS}؛ پیش‌فرض {PRAYER_DEFAULT_DAYS})",
            ),
        ],
        responses={200: PrayerTimesSerializer},
    )
    def get(self, request: Request) -> Response:
        """اوقات امروز و روزهای پیش رو (کش کوتاه؛ فرانت روز جاری را خودش برمی‌گزیند)."""
        try:
            days = int(request.query_params.get("days") or PRAYER_DEFAULT_DAYS)
        except ValueError:
            days = PRAYER_DEFAULT_DAYS
        days = max(1, min(PRAYER_MAX_DAYS, days))
        today = timezone.localdate()
        starts = lunar_month_starts()
        payload = {
            "city": prayer_times.CITY_LABEL,
            "method": prayer_times.METHOD_LABEL,
            "note": PRAYER_NOTE,
            "days": [
                {
                    "date": entry.day,
                    "label": format_jalali(entry.day, with_weekday=True),
                    **_lunar(entry.day, starts),
                    "slots": [
                        {"key": key, "label": label, "at": moment}
                        for key, label, moment in entry.slots()
                    ],
                }
                for entry in prayer_times.for_range(today, days)
            ],
            "naqqareh": {"title": NAQQAREH_TITLE, "text": NAQQAREH_TEXT},
        }
        response = Response(PrayerTimesSerializer(payload).data)
        response["Cache-Control"] = PRAYER_CACHE_CONTROL
        return response
