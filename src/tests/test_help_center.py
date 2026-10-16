"""تست‌های «میز راهنما» و «نزدیک‌ترین سانس آزاد» — دو چیزی که زائر می‌بیند.

سه خطر واقعی که این فایل می‌بندد:

    ۱) **راهنمای دروغ**: پاورقی سایت عددی می‌گفت («۱۴ نیم‌روز») که با سیاست زنده
       یکی نبود. اکنون همهٔ عددها از موتور خوانده می‌شوند و اینجا آزموده می‌شود
       که متن راهنما با سیاست عوض شود.
    ۲) **جای‌نگهدار ناپریده**: «{max_stay}» در چشم زائر، یعنی سامانه خراب است.
    ۳) **دکمه به در بسته**: پیشنهاد «نزدیک‌ترین سانس آزاد» باید سانسی باشد که
       موتور هم بپذیرد؛ اگر مرز مهلت آماده‌سازی رعایت نشود، زائر روی دکمه می‌زند
       و فرم همان سانس را رد می‌کند — بدترین تجربهٔ ممکن.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
import json
from string import Formatter
from typing import Any, Final

from django.test import Client
from django.urls import reverse
from django.utils import timezone
import pytest

from apps.core.jalali import format_jalali
from apps.inventory.lattice import stay_label
from apps.reservation import policy
from apps.reservation.availability import (
    STATUS_BUFFER,
    STATUS_FREE,
    STATUS_OCCUPIED,
    CellAvailability,
    DayAvailability,
    UnitAvailability,
)
from apps.web import help_content, presenters

pytestmark = pytest.mark.django_db


# ─── سازندگان ماتریس ساختگی (تست توابع خالص، بدون دیتابیس) ──────────────────
# لنگر زمانی: شش ساعت پس از «اکنون»، سر ساعت. چرا نه یک تاریخ ثابت؟ چون
# آزمون‌ها باید فردا هم سبز بمانند؛ مرز «مهلت آماده‌سازی» به اکنون وابسته است.
BASE_START: Final[datetime] = (timezone.now() + timedelta(hours=6)).replace(
    minute=0, second=0, microsecond=0
)


def _cell(
    index: int, kind: str, status: str, *, offset_hours: int = 0, base: datetime | None = None
) -> CellAvailability:
    """یک نیم‌روز ساختگی با آغاز معلوم روی محور شبکه."""
    start = (base or BASE_START) + timedelta(hours=offset_hours)
    return CellAvailability(
        index=index,
        kind=kind,
        label="روزانه" if kind == "day" else "شبانه",
        start=start,
        end=start + timedelta(hours=12),
        status=status,
    )


def _unit(code: str, statuses: Sequence[str], *, floor: int = 1) -> UnitAvailability:
    """یک واحد ساختگی با وضعیت‌های داده‌شده، به‌ترتیب نیم‌روزها."""
    cells = tuple(
        _cell(index, "day" if index % 2 == 0 else "night", status, offset_hours=index * 12)
        for index, status in enumerate(statuses)
    )
    return UnitAvailability(
        code=code,
        title=f"واحد {code}",
        floor_number=floor,
        unit_type="suite",
        capacity_total=4,
        cells=cells,
    )


def _day(index: int, units: Sequence[UnitAvailability]) -> DayAvailability:
    """یک شبانه‌روز ساختگی از ماتریس."""
    day = timezone.localtime(BASE_START).date() + timedelta(days=index)
    return DayAvailability(
        day=day,
        date_jalali=format_jalali(timezone.localtime(BASE_START).date()),
        weekday="ج",
        is_weekend=False,
        demand_level="normal",
        units=tuple(units),
        free_cells=sum(unit.free_cells for unit in units),
        total_cells=sum(len(unit.cells) for unit in units),
    )


def _split(
    cells: Sequence[CellAvailability], per_day: int = 2
) -> list[tuple[CellAvailability, ...]]:
    """تقسیم نیم‌روزهای یک واحد به شبانه‌روزها (برای ساخت ماتریس روز×واحد)."""
    return [tuple(cells[index : index + per_day]) for index in range(0, len(cells), per_day)]


def _matrix(unit_statuses: dict[str, Sequence[str]], *, per_day: int = 2) -> list[DayAvailability]:
    """ماتریس ساختگی: هر واحد ⇒ دنبالهٔ وضعیت‌های نیم‌روز در چند شبانه‌روز."""
    built = {code: _unit(code, statuses) for code, statuses in unit_statuses.items()}
    chunks = {code: _split(unit.cells, per_day) for code, unit in built.items()}
    days = {len(chunk) for chunk in chunks.values()}
    assert len(days) == 1, "همهٔ واحدها باید به یک اندازه شبانه‌روز داشته باشند"
    total_days = days.pop()
    return [
        _day(
            index,
            [
                UnitAvailability(
                    code=code,
                    title=unit.title,
                    floor_number=unit.floor_number,
                    unit_type=unit.unit_type,
                    capacity_total=unit.capacity_total,
                    cells=chunks[code][index],
                )
                for code, unit in built.items()
            ],
        )
        for index in range(total_days)
    ]


# ─── قرارداد محتوا ──────────────────────────────────────────────────────────
@pytest.fixture
def values() -> dict[str, str]:
    """مقادیر زندهٔ سیاست (خواندن سیاست به دیتابیس نیاز دارد، پس تابع‌محور)."""
    return help_content.policy_values()


def _placeholders(text: str) -> Iterator[str]:
    """نام جای‌نگهدارهای یک متن قالب‌پذیر."""
    for _literal, field, _spec, _conv in Formatter().parse(text):
        if field:
            yield field.split(".")[0].split("[")[0]


def test_every_placeholder_has_a_policy_value(values: dict[str, str]) -> None:
    """هیچ «{…}» ناپریده‌ای در متن راهنما نمی‌ماند."""
    problems: list[str] = []
    for item in help_content.all_items():
        texts = [item.question, item.answer, *item.steps, *item.tags]
        for text in texts:
            for field in _placeholders(text):
                if field not in values:
                    problems.append(f"{item.slug} → {{{field}}}")
    assert not problems, f"جای‌نگهدارهای بدون مقدار: {sorted(set(problems))}"


def test_slugs_are_unique_and_relations_resolve() -> None:
    """نشانه‌ها یکتا هستند، «بیشتر بخوانید» به پرسش موجود می‌رسد و خودارجاعی نیست."""
    slugs = [item.slug for item in help_content.all_items()]
    assert len(slugs) == len(set(slugs)), "نشانهٔ تکراری در راهنما"
    for item in help_content.all_items():
        assert item.slug not in item.related, f"خودارجاعی: {item.slug}"
        for slug in item.related:
            assert help_content.item_by_slug(slug) is not None, f"{item.slug} → {slug} یافت نشد"
        assert len(item.tags) >= 2, f"{item.slug} برچسب کافی برای جست‌وجو ندارد"


def test_actions_and_quick_steps_point_to_real_pages() -> None:
    """هر دکمهٔ راهنما به صفحه‌ای موجود می‌رسد (پیوند دروغ ممنوع)."""
    for item in help_content.all_items():
        if item.action is not None:
            assert reverse(item.action.url_name).startswith("/")
    for step in help_content.QUICK_STEPS:
        assert reverse(step["url_name"]).startswith("/")
    assert reverse("web:help_guide") == "/help/guide/"


def test_guide_numbers_follow_live_policy(values: dict[str, str]) -> None:
    """با عوض‌شدن سیاست، متن راهنما هم عوض می‌شود (راهنما از موتور نمی‌افتد)."""
    limits = help_content.item_by_slug("limits")
    assert limits is not None
    rendered = help_content.render(limits.answer, values)
    expected_max = stay_label(policy.policy_int("reservation.max_cells"))
    assert expected_max in rendered
    assert str(policy.policy_int("reservation.max_cells")) not in rendered, "رقم لاتین در متن"
    assert f"{policy.policy_int('reservation.max_cells_per_window')}" not in rendered


def test_staff_checklist_is_present_and_short() -> None:
    """چک‌لیست میز پذیرش: پنج گام، بدون جای‌نگهدار ناپریده."""
    assert len(help_content.STAFF_CHECKLIST) == 5
    for row in help_content.STAFF_CHECKLIST:
        assert row["title"] and row["text"]
        assert not list(_placeholders(row["text"]))


# ─── جست‌وجو ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("نیم‌روز", "halfday"),  # نیم‌فاصله
        ("نيمروز", "halfday"),  # «ي» عربی
        ("نیم روز", "halfday"),  # فاصلهٔ ساده
        ("لغو", "cancel"),
        ("لغو دیرهنگام", "late-cancel"),
        ("پیامک نیامد", "sms-missing"),
        ("قبول پیشنهاد", "offer"),
        ("سهمیه سالانه", "limits"),
        ("کد ملی", "profile"),
    ],
)
def test_search_finds_the_right_answer(query: str, expected: str, values: dict[str, str]) -> None:
    """جست‌وجو با شکل‌های نگارشی مختلف کار می‌کند و پرسش درست را بالا می‌آورد."""
    matches = help_content.search(query, values=values)
    assert matches, f"برای «{query}» چیزی پیدا نشد"
    assert matches[0].item.slug == expected, [match.item.slug for match in matches]


def test_search_is_and_based_and_safe_on_noise(values: dict[str, str]) -> None:
    """همهٔ واژه‌ها باید پیدا شوند؛ ورودی بی‌ربط و خالی نتیجهٔ خالی می‌دهد."""
    assert help_content.search("", values=values) == []
    assert help_content.search("   ", values=values) == []
    assert help_content.search("ققق ززز", values=values) == []
    assert help_content.search("لغو زززقق", values=values) == []
    matches = help_content.search("لغو", values=values, limit=2)
    assert len(matches) <= 2
    assert all(match.snippet for match in matches)


# ─── نماها ──────────────────────────────────────────────────────────────────
def test_help_center_renders_with_faq_structured_data(client: Client) -> None:
    """میز راهنما ۲۰۰ می‌دهد، همهٔ دسته‌ها را می‌آورد و دادهٔ ساخت‌یافته معتبر دارد."""
    response = client.get(reverse("web:help"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "میز راهنما" in body
    assert "application/ld+json" in body
    payload = json.loads(_extract_jsonld(body))
    assert payload["@type"] == "FAQPage"
    assert payload["inLanguage"] == "fa"
    assert len(payload["mainEntity"]) == len(help_content.faq_payload())
    for question in payload["mainEntity"]:
        assert question["name"] and question["acceptedAnswer"]["text"]
    for category in help_content.CATEGORIES:
        assert category.title in body


def _extract_jsonld(body: str) -> str:
    """جداکردن محتوای تگ ``application/ld+json`` از HTML صفحه."""
    start = body.index("application/ld+json") + len("application/ld+json")
    start = body.index(">", start) + 1
    end = body.index("</script>", start)
    return body[start:end].strip()


def test_help_search_and_filter_pages(client: Client) -> None:
    """جست‌وجوی صفحه‌ای و فیلتر دسته کار می‌کنند؛ دستهٔ ناشناس خطا نمی‌دهد."""
    search = client.get(reverse("web:help"), {"q": "لغو"})
    assert search.status_code == 200
    assert "نتیجهٔ جست‌وجو برای «لغو»" in search.content.decode()

    empty = client.get(reverse("web:help"), {"q": "ققق ززز"})
    assert empty.status_code == 200
    assert "پاسخی برای" in empty.content.decode()

    filtered = client.get(reverse("web:help"), {"cat": "booking"})
    body = filtered.content.decode()
    assert filtered.status_code == 200
    assert "رزرو سانس" in body
    assert "دستهٔ «پیش از سفر»" not in body

    unknown = client.get(reverse("web:help"), {"cat": "nope"})
    assert unknown.status_code == 200


def test_printable_guide_has_checklist_and_live_rules(client: Client) -> None:
    """برگ چاپی: چک‌لیست پذیرش، قواعد زنده و بدون دکمهٔ اضافه."""
    response = client.get(reverse("web:help_guide"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "چک‌لیست میز پذیرش" in body
    assert "گام ۵" in body
    assert "چاپ این برگ" in body
    values = help_content.policy_values()
    assert values["max_stay"] in body
    assert values["cancel_hours"] in body


def test_contact_block_never_invents_a_phone(settings: Any, client: Client) -> None:
    """اگر شمارهٔ پذیرش تنظیم نشده باشد، هیچ شمارهٔ ساختگی چاپ نمی‌شود."""
    settings.SUPPORT_PHONE = ""
    settings.SUPPORT_ADDRESS = ""
    body = client.get(reverse("web:help")).content.decode()
    assert help_content.contact()["phone"] == ""
    assert "هنوز در تنظیمات استقرار ثبت نشده" in body

    settings.SUPPORT_PHONE = "05138561000"
    settings.SUPPORT_ADDRESS = "مشهد، خیابان طبرسی"
    body = client.get(reverse("web:help")).content.decode()
    assert "۰۵۱۳۸۵۶۱۰۰۰" in body  # ارقام فارسی، نه لاتین
    assert "مشهد، خیابان طبرسی" in body


# ─── نزدیک‌ترین سانس آزاد ───────────────────────────────────────────────────
def test_free_windows_finds_the_first_run_per_unit() -> None:
    """نخستین رانِ پیوستهٔ آزاد پیدا می‌شود؛ ران کوتاه‌تر از کوتاه‌ترین اقامت رد می‌شود."""
    matrix = _matrix(
        {
            "A1": [STATUS_OCCUPIED, STATUS_OCCUPIED, STATUS_FREE, STATUS_FREE, STATUS_BUFFER],
            "A2": [STATUS_OCCUPIED, STATUS_FREE, STATUS_OCCUPIED, STATUS_FREE, STATUS_FREE],
        }
    )
    windows = presenters.free_windows(matrix, min_cells=2, max_cells=10)
    by_unit = {window.unit_code: window for window in windows}
    assert set(by_unit) == {"A1", "A2"}
    # A1 و A2 هر دو یک ران دو سلولی دارند؛ هر نامزد دقیقاً همان سانسی است که
    # نخستین سلول آزادِ ران آغاز می‌کند (نه زودتر، نه دیرتر).
    for code in ("A1", "A2"):
        window = by_unit[code]
        assert window.cells == 2
        assert window.stay_label == stay_label(2)
        assert window.day == timezone.localtime(window.starts_at).date()
        assert window.period in {"day", "night"}
    assert by_unit["A1"].starts_at < by_unit["A2"].starts_at or (
        by_unit["A1"].starts_at == by_unit["A2"].starts_at
    )


def test_free_windows_respects_min_cells_and_caps_length() -> None:
    """کوتاه‌ترین اقامتِ سیاست تعیین‌کننده است و طول پیشنهاد به سقف سیاست می‌رسد."""
    matrix = _matrix({"A1": [STATUS_FREE] * 6})
    assert presenters.free_windows(matrix, min_cells=7) == []
    capped = presenters.free_windows(matrix, min_cells=1, max_cells=3)
    assert capped[0].cells == 3
    assert capped[0].starts_at == matrix[0].units[0].cells[0].start


def test_free_windows_respects_the_lead_time_cutoff() -> None:
    """سانسی که آغازش گذشته (یا در مهلت آماده‌سازی است) پیشنهاد نمی‌شود."""
    matrix = _matrix({"A1": [STATUS_FREE] * 8})
    cells = matrix[0].units[0].cells
    start = cells[0].start
    as_is = presenters.free_windows(matrix, min_cells=1)
    assert as_is[0].starts_at == start

    later = presenters.free_windows(matrix, min_cells=1, not_before=start + timedelta(hours=13))
    assert later[0].starts_at == start + timedelta(hours=24), "پیشنهاد باید سانس بعدی باشد"

    beyond = presenters.free_windows(matrix, min_cells=1, not_before=start + timedelta(days=3))
    assert beyond[0].starts_at >= start + timedelta(days=3)

    horizon = presenters.free_windows(matrix, min_cells=1, not_before=start + timedelta(days=9))
    assert horizon == [], "خارج از افق ماتریس، پیشنهادی نیست"
    assert presenters.free_windows([], min_cells=1) == []
    assert presenters.next_free_window([]) is None


def test_next_free_window_picks_the_earliest_across_units() -> None:
    """میان واحدها، نزدیک‌ترین آغاز برنده است و هر واحد تنها یک نامزد دارد."""
    matrix = _matrix(
        {
            "A1": [STATUS_OCCUPIED, STATUS_OCCUPIED, STATUS_FREE, STATUS_FREE],
            "A2": [STATUS_FREE, STATUS_FREE, STATUS_FREE, STATUS_FREE],
        }
    )
    winner = presenters.next_free_window(matrix, min_cells=1)
    assert winner is not None and winner.unit_code == "A2"
    all_windows = presenters.free_windows(matrix, min_cells=1)
    assert len(all_windows) == 2


def test_home_offers_a_working_booking_link(units: dict[str, Any], client: Client) -> None:
    """صفحهٔ خانه دکمهٔ «رزرو همین سانس» را با پارامترهای همان فرم می‌دهد."""
    response = client.get(reverse("web:home"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "نزدیک‌ترین سانس آزاد" in body
    assert "/book/" in body
    assert "period=" in body and "cells=" in body and "day=" in body


def test_units_page_shows_a_window_per_unit(units: dict[str, Any], client: Client) -> None:
    """هر کارت واحد نشان «آزاد از …» یا پیام صریح «سانس آزاد ندارد» می‌گیرد."""
    response = client.get(reverse("web:units"))
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("آزاد از") >= 1
    assert "نیم‌روز آزاد تا" in body


def test_booking_link_from_home_is_accepted_by_the_form(
    units: dict[str, Any], client: Client
) -> None:
    """زنجیرهٔ کامل: پیشنهاد خانه ⇒ همان پارامترها ⇒ فرم رزرو، بدون «سانس نامعتبر»."""
    from apps.web.forms import BookingForm

    response = client.get(reverse("web:home"))
    body = response.content.decode()
    marker = "/book/"
    start = body.index(marker)
    href = body[start : body.index('"', start)]
    path, _, query = href.partition("?")
    params = dict(pair.split("=") for pair in query.split("&") if "=" in pair)
    assert {"day", "period", "cells"} <= set(params)
    form = BookingForm(
        {
            "unit_code": path.rstrip("/").split("/")[-1],
            "day": params["day"],
            "period": params["period"],
            "cells": params["cells"],
            "guests_count": 2,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["start"] >= timezone.now() + timedelta(
        hours=policy.policy_int("reservation.min_lead_time_hours")
    )
