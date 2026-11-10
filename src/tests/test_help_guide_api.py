"""قرارداد API صفحهٔ «راهنمای زائر» (P-08 فرانت): راهنمای کامل، بازخورد پاسخ‌ها، اوقات شرعی.

خطرهایی که این فایل می‌بندد:
    ۱) **دو حقیقت**: جدول قواعد برگ چاپی SSR و صفحهٔ فرانت باید از یک منبع بیایند؛ اگر یکی
       عدد سیاست را سخت‌کد کند، روزی که سیاست عوض شود دو برگ دو چیز می‌گویند.
    ۲) **رأی بی‌صاحب**: بازخورد برای پرسشی که وجود ندارد نباید جدول را پر کند.
    ۳) **اذان غلط**: اوقات شرعی با جدول رسمی منتشرشدهٔ مشهد سنجیده می‌شود (اختلاف ≤ ۱ دقیقه).
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
import pytest

from apps.core import prayer_times
from apps.reservation import policy
from apps.web import help_content
from apps.web.admin import vote_summary
from apps.web.api_views import HelpFeedbackThrottle
from apps.web.models import HelpVote

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

TEHRAN = ZoneInfo("Asia/Tehran")


# ─── راهنمای کامل ───────────────────────────────────────────────────────────
def test_help_api_carries_everything_the_guide_page_needs(api: Any) -> None:
    """قواعد زنده، چک‌لیست پذیرش، آمار، تاریخ، پرسش‌های FAQPage و نشان گام‌ها."""
    response = api.get("/api/v1/help")
    assert response.status_code == 200
    assert response["Cache-Control"].startswith("public")
    body = response.json()
    values = help_content.policy_values()

    assert body["values"] == values
    assert [row["key"] for row in body["rules"]] == [key for key, _, _ in help_content.RULES]
    rules = {row["key"]: row for row in body["rules"]}
    assert rules["max_stay"]["value"] == values["max_stay"]
    assert values["cancel_hours"] in rules["cancel"]["value"]
    assert values["quota_days"] in rules["quota"]["label"]

    assert len(body["staff_checklist"]) == len(help_content.STAFF_CHECKLIST)
    assert body["stats"] == {
        "categories": len(help_content.CATEGORIES),
        "questions": len(help_content.all_items()),
        "steps": len(help_content.QUICK_STEPS),
        "contact_ready": bool(help_content.contact()["phone"]),
    }
    assert body["today"] == help_content.today_label()
    slugs = {item["slug"] for category in body["categories"] for item in category["items"]}
    assert set(body["faq"]) <= slugs
    assert len(body["faq"]) == len(help_content.FAQ_SLUGS)

    badges = [step["badge"] for step in body["quick_steps"]]
    assert all(badge and "{" not in badge for badge in badges)
    assert any(values["horizon_days"] in badge for badge in badges)
    assert set(body["contact"]) == {"phone", "phone_display", "address"}


def test_rules_follow_a_policy_change(values_override: Any) -> None:
    """عوض شدن سیاست ⇒ عوض شدن ردیف جدول (هیچ عدد سخت‌کدی)."""
    values_override("reservation.cancel_free_before_hours", 48)
    rows = {row["key"]: row for row in help_content.rules_table(help_content.policy_values())}
    assert "۴۸" in rows["cancel"]["value"]


@pytest.fixture
def values_override(monkeypatch: pytest.MonkeyPatch) -> Any:
    """جایگزینی موقت یک مقدار سیاست (بی‌دست‌زدن به دیتابیس)."""
    original = policy.policy_int
    overrides: dict[str, int] = {}

    def fake(key: str) -> Any:
        return overrides.get(key, original(key))

    monkeypatch.setattr(policy, "policy_int", fake)

    def setter(key: str, value: int) -> None:
        overrides[key] = value

    return setter


def test_every_rule_and_badge_template_has_values() -> None:
    """جای‌نگهدار ناپریده در جدول قواعد و نشان گام‌ها ممنوع."""
    values = help_content.default_values()
    for row in help_content.rules_table(values):
        assert "{" not in row["label"] + row["value"]
    for step in help_content.quick_steps(values):
        assert "{" not in step["badge"]


def test_printable_guide_reads_the_same_rules_table(client: Client) -> None:
    """برگ چاپی SSR همان ردیف‌های API را دارد (یک منبع)."""
    body = client.get(reverse("web:help_guide")).content.decode()
    for row in help_content.rules_table(help_content.policy_values()):
        assert row["label"] in body
        assert row["value"] in body


def test_content_fixes_stay_fixed(api: Any) -> None:
    """درس‌های بازبینی محتوا: سقف «حداکثر» است و زائر غیرایرانی دیده می‌شود."""
    items = {
        item["slug"]: item
        for category in api.get("/api/v1/help").json()["categories"]
        for item in category["items"]
    }
    assert "حداکثر" in items["limits"]["answer"]
    assert "دست‌کم" not in items["limits"]["answer"]
    assert "گذرنامه" in items["profile"]["answer"]
    assert "می‌آماده" not in items["guests"]["answer"]


# ─── بازخورد ────────────────────────────────────────────────────────────────
def test_feedback_is_anonymous_and_validated(api: Any) -> None:
    """رأی معتبر ⇒ ۲۰۴ و یک ردیف بی‌شناسه؛ نشانهٔ ناشناخته ⇒ ۴۲۲ و هیچ ردیف."""
    ok = api.post("/api/v1/help/feedback", {"slug": "cancel", "helpful": False}, format="json")
    assert ok.status_code == 204
    vote = HelpVote.objects.get()
    assert (vote.slug, vote.helpful) == ("cancel", False)
    assert {field.name for field in HelpVote._meta.fields} == {
        "id",
        "slug",
        "helpful",
        "created_at",
    }

    bad = api.post("/api/v1/help/feedback", {"slug": "nope", "helpful": True}, format="json")
    assert bad.status_code == 422
    missing = api.post("/api/v1/help/feedback", {"slug": "cancel"}, format="json")
    assert missing.status_code == 422
    assert HelpVote.objects.count() == 1


def test_feedback_has_a_rate_limit(api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """سقف ضد ربات: در تولید پیکربندی شده و پس از سقف ⇒ ۴۲۹ بی‌ثبت رأی."""
    base = (Path(settings.BASE_DIR) / "config" / "settings" / "base.py").read_text()
    assert f'"{HelpFeedbackThrottle.scope}": "' in base
    cache.clear()
    monkeypatch.setattr(
        HelpFeedbackThrottle, "THROTTLE_RATES", {HelpFeedbackThrottle.scope: "2/hour"}
    )
    codes = [
        api.post(
            "/api/v1/help/feedback", {"slug": "join", "helpful": True}, format="json"
        ).status_code
        for _ in range(3)
    ]
    assert codes == [204, 204, 429]
    assert HelpVote.objects.count() == 2
    cache.clear()


def test_admin_summary_puts_the_least_helpful_answer_first() -> None:
    """خلاصهٔ پنل: نامفیدترین بالا، با متن پرسش و درصد رضایت."""
    HelpVote.objects.bulk_create(
        [HelpVote(slug="cancel", helpful=False)] * 3
        + [HelpVote(slug="cancel", helpful=True)]
        + [HelpVote(slug="signup", helpful=True)] * 2
    )
    rows = vote_summary(HelpVote.objects.all())
    assert [row["slug"] for row in rows] == ["cancel", "signup"]
    assert rows[0]["ratio"] == 25
    assert rows[0]["question"] == help_content.item_by_slug("cancel").question  # type: ignore[union-attr]
    assert rows[1]["ratio"] == 100


# ─── اوقات شرعی ─────────────────────────────────────────────────────────────
#: جدول رسمی منتشرشدهٔ مشهد (شهرآرانیوز و پیشخوانک، ۱۴۰۵): (صبح، طلوع، ظهر، غروب، مغرب).
OFFICIAL: dict[date, tuple[str, ...]] = {
    date(2026, 7, 10): ("02:36", "04:22", "11:37", "18:51", "19:12"),
    date(2026, 10, 12): ("04:12", "05:36", "11:18", "17:00", "17:18"),
}


def _minutes(moment: datetime) -> int:
    local = moment.astimezone(TEHRAN)
    return local.hour * 60 + local.minute


@pytest.mark.parametrize("day", sorted(OFFICIAL))
def test_prayer_times_match_the_official_mashhad_table(day: date) -> None:
    """اختلاف هر وقت با جدول رسمی حداکثر یک دقیقه."""
    computed = prayer_times.for_day(day)
    keys = ("fajr", "sunrise", "dhuhr", "sunset", "maghrib")
    for key, expected in zip(keys, OFFICIAL[day], strict=True):
        hour, minute = (int(part) for part in expected.split(":"))
        assert abs(_minutes(getattr(computed, key)) - (hour * 60 + minute)) <= 1, key


def test_midnight_is_halfway_from_sunset_to_next_fajr() -> None:
    """نیمه‌شب شرعی (پاییز ۱۴۰۵: ۲۲:۳۶ رسمی) میان غروب و اذان صبح فرداست."""
    computed = prayer_times.for_day(date(2026, 10, 12))
    assert abs(_minutes(computed.midnight) - (22 * 60 + 36)) <= 1
    order = [moment for _, _, moment in computed.slots()]
    assert order == sorted(order)
    assert all(moment.second == 0 for moment in order)


def test_prayer_api_shape_bounds_and_cache(api: Any) -> None:
    """از امروز، با مرز ۱..۱۴ روز، برچسب فارسی و نقاره‌خانه."""
    body = api.get("/api/v1/prayer-times?days=3").json()
    assert len(body["days"]) == 3
    first = body["days"][0]
    assert [slot["key"] for slot in first["slots"]] == [key for key, _ in prayer_times.SLOTS]
    assert datetime.fromisoformat(first["slots"][0]["at"]).utcoffset() is not None
    assert "نقاره" in body["naqqareh"]["title"]
    assert "محرم" in body["naqqareh"]["text"]

    assert len(api.get("/api/v1/prayer-times?days=99").json()["days"]) == 14
    assert len(api.get("/api/v1/prayer-times?days=0").json()["days"]) == 1
    fallback = api.get("/api/v1/prayer-times?days=x")
    assert len(fallback.json()["days"]) == 7
    assert fallback["Cache-Control"].startswith("public")


def test_prayer_times_are_stable_through_the_year() -> None:
    """هر روز سال: ترتیب درست و همه در همان روز (نیمه‌شب پیش از ۰۰:۰۰)."""
    start = date(2026, 3, 21)
    for entry in prayer_times.for_range(start, 365):
        moments = [moment for _, _, moment in entry.slots()]
        assert moments == sorted(moments)
        assert all(moment.astimezone(TEHRAN).date() == entry.day for moment in moments)
        assert time(2, 0) < entry.fajr.astimezone(TEHRAN).time() < time(5, 30)
