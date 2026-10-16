"""تست تقویم جلالی — لنگرهای واقعی، رفت‌وبرگشت، و مرزهای کبیسه.

این تست‌ها قلب اعتبار ماژول جلالی هستند: تبدیل دوطرفه باید بدون خطا باشد،
وگرنه تقویم کاربران اشتباه نمایش داده می‌شود.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.core import jalali


@pytest.mark.unit
@pytest.mark.parametrize(
    ("gregorian", "expected"),
    [
        (date(2026, 10, 15), (1405, 7, 23)),  # لنگر پروژه
        (date(2026, 3, 21), (1405, 1, 1)),  # نوروز ۱۴۰۵
        (date(2025, 3, 21), (1404, 1, 1)),  # نوروز ۱۴۰۴
        (date(2025, 3, 20), (1403, 12, 30)),  # آخرین روز سال کبیسهٔ ۱۴۰۳
        (date(2000, 1, 1), (1378, 10, 11)),
        (date(1979, 2, 11), (1357, 11, 22)),  # انقلاب اسلامی
        (date(1993, 6, 21), (1372, 3, 31)),
    ],
)
def test_known_gregorian_to_jalali(gregorian: date, expected: tuple[int, int, int]) -> None:
    """تبدیل میلادی به جلالی برای تاریخ‌های شناخته‌شده."""
    assert jalali.gregorian_to_jalali(gregorian) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("jy", "jm", "jd", "expected"),
    [
        (1405, 7, 23, date(2026, 10, 15)),
        (1405, 1, 1, date(2026, 3, 21)),
        (1403, 12, 30, date(2025, 3, 20)),
        (1357, 11, 22, date(1979, 2, 11)),
    ],
)
def test_known_jalali_to_gregorian(jy: int, jm: int, jd: int, expected: date) -> None:
    """تبدیل جلالی به میلادی برای تاریخ‌های شناخته‌شده."""
    assert jalali.jalali_to_gregorian(jy, jm, jd) == expected


@pytest.mark.unit
def test_roundtrip_is_lossless_over_two_years() -> None:
    """رفت‌وبرگشت روزبه‌روز در بازهٔ دو ساله هرگز خطا نمی‌دهد."""
    current = date(2024, 1, 1)
    end = date(2026, 1, 1)
    while current < end:
        jy, jm, jd = jalali.gregorian_to_jalali(current)
        assert jalali.jalali_to_gregorian(jy, jm, jd) == current, current
        current += timedelta(days=1)


@pytest.mark.unit
def test_leap_years_match_known_facts() -> None:
    """سال‌های کبیسهٔ شناخته‌شده درست تشخیص داده می‌شوند."""
    assert jalali.is_leap_year(1403) is True
    assert jalali.month_length(1403, 12) == 30
    assert jalali.month_length(1404, 12) == 29
    assert jalali.month_length(1405, 1) == 31
    assert jalali.month_length(1405, 7) == 30


@pytest.mark.unit
def test_invalid_inputs_are_rejected() -> None:
    """ورودی نامعتبر باید صریحاً رد شود — نه سکوت."""
    with pytest.raises(ValueError, match="ماه جلالی"):
        jalali.jalali_to_gregorian(1405, 13, 1)
    with pytest.raises(ValueError, match="روز جلالی نامعتبر"):
        jalali.jalali_to_gregorian(1404, 12, 30)  # ۱۴۰۴ کبیسه نیست
    with pytest.raises(ValueError, match="خارج از دامنه"):
        jalali.jalali_to_gregorian(9999, 1, 1)


@pytest.mark.unit
def test_formatting_and_helpers() -> None:
    """قالب‌بندی فارسی، نام روز، فصل و آخر هفته."""
    sample = date(2026, 10, 15)  # پنجشنبه
    assert jalali.format_jalali(sample, numeric=True) == "۱۴۰۵/۰۷/۲۳"
    assert jalali.format_jalali(sample) == "۲۳ مهر ۱۴۰۵"
    assert jalali.format_jalali(sample, with_weekday=True).startswith("پنجشنبه")
    assert jalali.weekday_name(sample) == "پنجشنبه"
    assert jalali.season_of(sample) == "پاییز"
    assert jalali.is_weekend(sample) is True
    assert jalali.is_weekend(date(2026, 10, 17)) is False  # شنبه


@pytest.mark.unit
def test_jalali_digits_contract() -> None:
    """قرارداد ارقام: پیش‌فرض فارسی، لاتین فقط با درخواست صریح (قرارداد ماشینی).

    این تست «قاعده» را قفل می‌کند، چون شکستنش بی‌صدا است: پیش‌تر خروجی پیش‌فرض
    لاتین بود و هر فراخوان باید ``|fa`` می‌زد؛ آن‌که فراموش می‌کرد، «جمعه 24 مهر
    1405» را به زائر نشان می‌داد (نقص دیدنی فاز ۹).
    """
    sample = date(2026, 10, 15)
    assert jalali.format_jalali(sample, numeric=True, latin_digits=True) == "1405/07/23"
    assert jalali.format_jalali(sample, latin_digits=True) == "23 مهر 1405"
    for output in (
        jalali.format_jalali(sample),
        jalali.format_jalali(sample, numeric=True),
        jalali.format_jalali(sample, with_weekday=True),
    ):
        assert not any(character.isascii() and character.isdigit() for character in output)


@pytest.mark.unit
def test_month_range_and_jdn_helpers() -> None:
    """بازهٔ ماه جلالی و ابزارهای روز مطلق."""
    first, last = jalali.jalali_month_range(1405, 7)
    assert first == date(2026, 9, 23)
    assert last == date(2026, 10, 22)
    assert jalali.jdn_of(date(2026, 10, 15)) + 1 == jalali.jdn_of(date(2026, 10, 16))
    assert jalali.date_from_jdn(jalali.jdn_of(date(2026, 10, 15))) == date(2026, 10, 15)
