"""قاعدهٔ ارقام — «هیچ شمارهٔ لاتینی در چشم زائر».

ماجرای واقعی (فاز ۹): قالب‌بندی تاریخ پیش‌فرضش ارقام لاتین بود و صفحهٔ واحدها
«طبقهٔ 3» را نشان می‌داد. هیچ تستی نمی‌گرفت، چون **هر تابع به‌تنهایی درست بود**:
``format_jalali`` کار می‌کرد، قالب هم درست رندر می‌شد؛ فقط <em>ترکیب</em> آن‌ها
عدد لاتین را داخل متن فارسی می‌گذاشت. پس این تست، خروجی *نهایی* را می‌سنجد:

    ۱) HTML صفحه‌های واقعی گرفته می‌شود (نما، قالب، فیلترها — همه با هم).
    ۲) متن دیدنی جدا می‌شود؛ بلوک‌های «ltr» (شناسه‌های ماشینی) کنار گذاشته می‌شوند.
    ۳) شناسه‌های عمداً لاتین (کد واحد، کد رزرو، تلفن پوشانده) پوشانده می‌شوند.
    ۴) اگر رقم لاتینی نزدیک متن فارسی بماند، تست شکست می‌خورد.

و برای اینکه «قاعده‌ای که هیچ‌چیز نمی‌گیرد» نداشته باشیم، خودِ آشکارساز هم در
تست دوم با یک قطعهٔ عمداً خراب آزموده می‌شود.
"""

from __future__ import annotations

from html import unescape
import re
from typing import Final

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PERSIAN_LETTERS: Final[re.Pattern[str]] = re.compile(r"[\u0600-\u06FF]")
# فقط ارقام لاتین — نکتهٔ دقیق: ``\d`` در پایتون ارقام فارسی/عربی را هم می‌گیرد
# و آشکارساز را بی‌اثر می‌کند (اولین اجرا همین تله را نشان داد).
ASCII_DIGITS: Final[re.Pattern[str]] = re.compile(r"[0-9]+")
LTR_SPAN: Final[re.Pattern[str]] = re.compile(
    r"<span[^>]*class=\"[^\"]*\bltr\b[^\"]*\"[^>]*>.*?</span>", re.DOTALL | re.IGNORECASE
)
SCRIPT_OR_STYLE: Final[re.Pattern[str]] = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
TAG: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")

# شناسه‌هایی که عمداً لاتین می‌مانند: کد یکتای واحد، کد رزرو (برای مکالمهٔ
# تلفنی)، تلفن پوشانده، کلید درخواست/ULID. لاتین‌بودن این‌ها *قاعده* است، نه
# نقص: با ارقام فارسی، جست‌وجو و تطبیق در پایگاه‌داده و کپی‌کردن در تماس تلفنی
# خراب می‌شود.
MACHINE_TOKENS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(?:A1|A2|B1|B2|B3)\b"),  # کد واحد
    re.compile(r"\bM-\d{4}-\d{4}\b"),  # کد رزرو
    re.compile(r"\bMH[-\w]*\b"),
    re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b"),  # ULID
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    re.compile(r"0\d{3}\*{3}\d{4}"),  # تلفن پوشانده
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # نشانی شبکه
    re.compile(r"\b(?:v|V)\d+(?:\.\d+)*\b"),  # نسخهٔ نرم‌افزار
    re.compile(r"\b(?:SHA|MD5|TLS|ISO|HTTP|TOTP|RFC)-?\d[\w.-]*\b"),  # نام استاندارد
    re.compile(r"\b[A-Za-z_]+(?:\.[A-Za-z_]+)+\b"),  # کلید تنظیمات/سیاست مثل lattice.cell_hours
    re.compile(r"\b(?:True|False|None)\b"),  # مقدار بولین ماشینی
)

GUEST_PAGES: Final[tuple[str, ...]] = (
    "/",
    "/units/",
    "/units/A1/",
    "/calendar/",
    "/login/",
)

BOARD_PAGES: Final[tuple[str, ...]] = (
    "/board/",
    "/board/insights/",
    "/board/waitlist/",
    "/board/policies/",
    "/my/",
)


def visible_text(html: str) -> str:
    """متن دیدنی یک صفحه — بدون تگ، بدون اسکریپت، بدون بلوک‌های ltr."""
    without_ltr = LTR_SPAN.sub(" ", SCRIPT_OR_STYLE.sub(" ", html))
    text = unescape(TAG.sub(" ", without_ltr))
    return re.sub(r"\s+", " ", text.replace("\u200c", " "))


def mask_machine_tokens(text: str) -> str:
    """پوشاندن شناسه‌های لاتینِ مجاز تا در سنجش، رقم لاتین شمرده نشوند."""
    masked = text
    for pattern in MACHINE_TOKENS:
        masked = pattern.sub(lambda match: "\u0000" * len(match.group(0)), masked)
    return masked


def latin_digit_leaks(text: str, *, window: int = 30) -> list[str]:
    """قطعه‌هایی از متن که رقم لاتین نزدیک حرف فارسی دارند."""
    masked = mask_machine_tokens(text)
    leaks: list[str] = []
    for match in ASCII_DIGITS.finditer(masked):
        start = max(0, match.start() - window)
        end = min(len(masked), match.end() + window)
        if PERSIAN_LETTERS.search(masked[start:end]):
            leaks.append(re.sub(r"\u0000", "·", masked[start:end]).strip())
    return leaks


LATIN_DECIMAL_BETWEEN_PERSIAN: Final[re.Pattern[str]] = re.compile(r"[۰-۹][.,][۰-۹]")


def decimal_separator_leaks(text: str) -> list[str]:
    """جداشدنی‌های لاتین میان ارقام فارسی: «۲.۶» به‌جای «۲٫۶»."""
    return [match.group(0) for match in LATIN_DECIMAL_BETWEEN_PERSIAN.finditer(text)]


def test_detector_catches_a_known_bad_fragment() -> None:
    """خودِ آشکارساز آزموده می‌شود؛ وگرنه ممکن است بی‌اثر باشد."""
    bad = "<p>طبقهٔ 3 — سوئیت با ۲ تخت</p>"
    good = "<p>طبقهٔ ۳ — سوئیت با ۲ تخت · کد M-1405-0231</p>"
    ltr = '<p>کلید درخواست: <span class="ltr">01HX9Z7KQ2</span></p>'
    assert latin_digit_leaks(visible_text(bad)), "باید «طبقهٔ 3» را بگیرد"
    assert not latin_digit_leaks(visible_text(good)), "نباید برای متن درست مثبت کاذب بدهد"
    assert not latin_digit_leaks(visible_text(ltr)), "شناسهٔ ماشینی داخل ltr مجاز است"


def test_guest_pages_have_no_latin_digits_in_persian_text(client, units) -> None:  # type: ignore[no-untyped-def]
    """صفحه‌های عمومی زائر: هیچ رقم لاتینی داخل متن فارسی نماند."""
    problems: dict[str, list[str]] = {}
    for path in GUEST_PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        leaks = latin_digit_leaks(visible_text(response.content.decode("utf-8")))
        leaks += decimal_separator_leaks(visible_text(response.content.decode("utf-8")))
        if leaks:
            problems[path] = leaks
    assert not problems, f"رقم لاتین در متن فارسی: {problems}"


def test_board_and_guest_area_pages_have_no_latin_digits(client, guest, operator, units) -> None:  # type: ignore[no-untyped-def]
    """صفحه‌های کارکنان و ناحیهٔ زائر (با ورود) هم همین قاعده را رعایت می‌کنند."""
    from apps.reservation import services as reservation_services

    start, end = _period(units["A1"])
    reservation_services.create_reservation(
        guest=guest,
        unit=units["A1"],
        start=start,
        end=end,
        guests_count=2,
        expected_arrival=None,
    )
    problems: dict[str, list[str]] = {}
    client.force_login(operator)
    for path in BOARD_PAGES:
        response = client.get(path)
        assert response.status_code == 200, f"{path} ⇒ {response.status_code}"
        leaks = latin_digit_leaks(visible_text(response.content.decode("utf-8")))
        leaks += decimal_separator_leaks(visible_text(response.content.decode("utf-8")))
        if leaks:
            problems[path] = leaks
    client.force_login(guest)
    for path in ("/my/", "/profile/"):
        response = client.get(path)
        assert response.status_code == 200, path
        leaks = latin_digit_leaks(visible_text(response.content.decode("utf-8")))
        leaks += decimal_separator_leaks(visible_text(response.content.decode("utf-8")))
        if leaks:
            problems[path] = leaks
    assert not problems, f"رقم لاتین در متن فارسی: {problems}"


def _period(unit) -> tuple[object, object]:  # type: ignore[no-untyped-def]
    """یک بازهٔ آیندهٔ معتبر روی شبکهٔ نیم‌روزی (۳ روز بعد، یک شبانه‌روز)."""
    from tests.factories import lattice_period

    return lattice_period(start_day=_days_ahead(3), cells=2)


def _days_ahead(days: int):  # type: ignore[no-untyped-def]
    """تاریخ میلادی چند روز بعد — از کارخانهٔ آزمون تا قاعده یک‌جا بماند."""
    from tests.factories import days_ahead

    return days_ahead(days)
