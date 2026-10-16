"""تبدیل و قالب‌بندی تقویم جلالی — بدون وابستگی بیرونی، اعتبارسنجی‌شدهٔ روزبه‌روز.

چرا پیاده‌سازی داخلی (و نه کتابخانهٔ بیرونی)؟
    • رفتار کتابخانه‌های تاریخ باید «قابل‌اثبات» باشد، نه «فرض‌شده».
    • این ماژول کوچک است، تابعی است، و در تست‌ها **روز‌به‌روز** با مرجع
      مستقل مقایسه می‌شود (۱۲۰۰ تا ۱۶۰۰ جلالی).
    • نمایش جلالی، الزام محصولی کارفرماست (اصل کاربرپسندی).

الگوریتم: چرخه‌های کبیسهٔ ۳۳ساله با جدول break (روش پروژهٔ jalaali) —
استانداردِ عملی تقویم هجری شمسی.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

from apps.core.text import PERSIAN_DECIMAL_SEPARATOR, to_persian_digits

PERSIAN_MONTHS: Final[tuple[str, ...]] = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)

PERSIAN_WEEKDAYS: Final[tuple[str, ...]] = (
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنجشنبه",
    "جمعه",
    "شنبه",
    "یک‌شنبه",
)

PERSIAN_WEEKDAYS_SHORT: Final[tuple[str, ...]] = ("د", "س", "چ", "پ", "ج", "ش", "ی")

PERSIAN_SEASONS: Final[tuple[str, ...]] = ("بهار", "تابستان", "پاییز", "زمستان")

# مرزهای چرخه‌های کبیسه — جدول مرجع الگوریتم
_BREAKS: Final[tuple[int, ...]] = (
    -61,
    9,
    38,
    199,
    426,
    686,
    756,
    818,
    1111,
    1181,
    1210,
    1635,
    2060,
    2097,
    2192,
    2262,
    2324,
    2394,
    2456,
    3178,
)

PERSIAN_EPOCH_GREGORIAN_YEAR: Final[int] = 621

# شش ماه نخست سال جلالی ۱۸۶ روز است (۶ × ۳۱)
_DAYS_FIRST_HALF: Final[int] = 186


def _jal_cal(jy: int) -> tuple[int, int, int]:
    """محاسبهٔ وضعیت کبیسه و روز شروع سال جلالی.

    بازگشت: ``(leap, gy, march)`` — کبیسه (۰ یعنی کبیسه)، سال میلادی، روز مارس.
    """
    if jy < _BREAKS[0] or jy >= _BREAKS[-1]:
        msg = f"سال جلالی خارج از دامنهٔ پشتیبانی: {jy}"
        raise ValueError(msg)

    gy = jy + PERSIAN_EPOCH_GREGORIAN_YEAR
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    for index in range(1, len(_BREAKS)):
        jm = _BREAKS[index]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += (jump // 33) * 8 + ((jump % 33) // 4)
        jp = jm
    n = jy - jp
    leap_j += (n // 33) * 8 + ((n % 33 + 3) // 4)
    if jump % 33 == 4 and jump - n == 4:  # noqa: PLR2004
        leap_j += 1
    leap_g = gy // 4 - ((gy // 100 + 1) * 3 // 4) - 150
    march = 20 + leap_j - leap_g

    if jump - n < 6:  # noqa: PLR2004
        n = n - jump + ((jump + 4) // 33) * 33
    leap = ((n + 1) % 33 - 1) % 4
    if leap == -1:
        leap = 4
    return leap, gy, march


def is_leap_year(jy: int) -> bool:
    """آیا سال جلالی کبیسه است؟"""
    leap, _, _ = _jal_cal(jy)
    return leap == 0


def month_length(jy: int, jm: int) -> int:
    """طول ماه جلالی — شش ماه نخست ۳۱ روز، شش ماه دوم ۳۰ و اسفند ۲۹/۳۰."""
    if not 1 <= jm <= 12:  # noqa: PLR2004
        msg = f"ماه جلالی باید بین ۱ و ۱۲ باشد: {jm}"
        raise ValueError(msg)
    if jm <= 6:  # noqa: PLR2004
        return 31
    if jm <= 11:  # noqa: PLR2004
        return 30
    return 30 if is_leap_year(jy) else 29


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    """تبدیل تاریخ جلالی به میلادی (با اعتبارسنجی کامل ورودی)."""
    if not 1 <= jm <= 12:  # noqa: PLR2004
        msg = f"ماه جلالی باید بین ۱ و ۱۲ باشد: {jm}"
        raise ValueError(msg)
    max_day = month_length(jy, jm)
    if not 1 <= jd <= max_day:
        msg = f"روز جلالی نامعتبر برای {jy}/{jm}: {jd} (بیشینه {max_day})"
        raise ValueError(msg)

    _, gy, march = _jal_cal(jy)
    days = (jm - 1) * 31 if jm <= 7 else (jm - 1) * 30 + 6  # noqa: PLR2004
    result = date(gy, 3, march) + timedelta(days=days + jd - 1)

    # تنظیم سال میلادی در صورت عبور از مرز
    if result.year < gy:  # pragma: no cover - محافظ دفاعی
        result = date(gy, 3, march) + timedelta(days=days + jd - 1)
    return result


def gregorian_to_jalali(value: date | datetime) -> tuple[int, int, int]:
    """تبدیل تاریخ میلادی به جلالی با شمارش روز مطلق.

    روش: هر سال جلالی در روز مشخصی از مارس میلادی آغاز می‌شود
    (``20 + leap_j - leap_g``). اختلاف شمارهٔ روز مطلق، فاصله از آغاز سال
    جلالی را می‌دهد؛ از آن، ماه و روز محاسبه می‌شود. این روش خطای انباشتی
    ندارد و با مرجع مستقل روزبه‌روز مقایسه شده است.
    """
    target_jdn = jdn_of(value if isinstance(value, date) else value.date())

    jy = value.year - PERSIAN_EPOCH_GREGORIAN_YEAR
    _, gy, march = _jal_cal(jy)
    start_jdn = jdn_of(date(gy, 3, march))

    if target_jdn < start_jdn:
        jy -= 1
        _, gy_prev, march_prev = _jal_cal(jy)
        start_jdn = jdn_of(date(gy_prev, 3, march_prev))

    offset = target_jdn - start_jdn
    if offset < _DAYS_FIRST_HALF:
        jm = 1 + offset // 31
        jd = 1 + offset % 31
    else:
        offset -= _DAYS_FIRST_HALF
        jm = 7 + offset // 30
        jd = 1 + offset % 30
    return jy, jm, jd


# ─── قالب‌بندی و ابزارهای ارائه ───────────────────────────────────────────────
def format_jalali(
    value: date | datetime,
    *,
    with_weekday: bool = False,
    numeric: bool = False,
    latin_digits: bool = False,
) -> str:
    """قالب‌بندی تاریخ به فارسی.

    نمونه‌ها: ``۱۴۰۵/۰۷/۲۳`` · ``۲۳ مهر ۱۴۰۵`` · ``پنجشنبه ۲۳ مهر ۱۴۰۵``

    قرارداد ارقام (درس واقعی فاز ۹): پیش‌فرض **ارقام فارسی** است، چون خروجی این
    تابع تقریباً همیشه به چشم زائر یا کارمند می‌رسد. پیش‌تر ارقام لاتین برمی‌گشت
    و هر فراخوان باید خودش ``|fa`` می‌زد؛ آن‌که فراموش می‌کرد، «جمعه 24 مهر 1405»
    را به زائر نشان می‌داد. حالا استثنا صریح است: تنها قراردادهای ماشینی
    (پاسخ‌های JSON و پارس‌کننده‌های بیرونی) ``latin_digits=True`` می‌دهند.
    """
    jy, jm, jd = gregorian_to_jalali(value)
    if numeric:
        text = f"{jy:04d}/{jm:02d}/{jd:02d}"
    else:
        text = f"{jd} {PERSIAN_MONTHS[jm - 1]} {jy}"
        if with_weekday:
            text = f"{weekday_name(value)} {text}"
    return text if latin_digits else to_persian_digits(text)


def clock_label(value: datetime) -> str:
    """ساعت محلی با ارقام فارسی — «۱۴:۳۰».

    چرا جدا از ``format_jalali``؟ چون متن‌های ساخته‌شده در پایتون (گواهی اقامت،
    مهر زمانی پیامک، برچسب لحظه) ساعت را جدا می‌سازند و همان‌جا هم ارقام لاتین
    لو می‌رفت. یک تابع، یک قاعده: ساعتِ نمایشی، فارسی‌رقم است.
    """
    # درون‌تابعی نگه داشته شده تا ``core`` در مسیرهای بی‌جنگو سبک بماند.
    from django.utils import timezone

    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return to_persian_digits(f"{local.hour:02d}:{local.minute:02d}")


def weekday_name(value: date | datetime) -> str:
    """نام روز هفته به فارسی."""
    return PERSIAN_WEEKDAYS[value.weekday()]


def weekday_short(value: date | datetime) -> str:
    """حرف نشانهٔ روز هفته (برای سرستون تقویم)."""
    return PERSIAN_WEEKDAYS_SHORT[value.weekday()]


def season_of(value: date | datetime) -> str:
    """فصل سال جلالی."""
    _, jm, _ = gregorian_to_jalali(value)
    return PERSIAN_SEASONS[(jm - 1) // 3]


def is_weekend(value: date | datetime) -> bool:
    """آیا روز، پنجشنبه یا جمعه است؟ (اوج تقاضای زائرسرا)"""
    return value.weekday() in (3, 4)


def date_from_jdn(jdn: int) -> date:
    """بازگشت از شمارهٔ روز مطلق یولیانی به تاریخ میلادی."""
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - 146097 * b // 4
    d = (4 * c + 3) // 1461
    e = c - 1461 * d // 4
    m = (5 * e + 2) // 153
    gd = e - (153 * m + 2) // 5 + 1
    gm = m + 3 - 12 * (m // 10)
    gy = 100 * b + d - 4800 + m // 10
    return date(gy, gm, gd)


def jdn_of(value: date) -> int:
    """شمارهٔ روز مطلق یولیانی — ابزار مقایسه و شمارش بازه‌ها."""
    a = (14 - value.month) // 12
    y = value.year + 4800 - a
    m = value.month + 12 * a - 3
    return value.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


SECONDS_PER_DAY: Final[int] = 24 * 60 * 60


def duration_days_label(start: datetime, end: datetime) -> str:
    """برچسب فارسی مدتِ اقامت بر حسب روز — «۰٫۵» برای نیم‌روز، «۳» برای سه روز.

    چرا این‌جا و نه در هر فراخوان؟ مدت اقامت در پیامک، گزارش اکسل و صفحهٔ رزرو
    تکرار می‌شود؛ اگر هر جا جداگانه حساب شود، روزی برای یک بازهٔ یکسان «۳ روز» و
    «۱٫۵ روز» با هم ظاهر می‌شوند. یک تابع، یک قاعده.

    ممیز فارسی و ارقام فارسی از ``apps.core.text`` می‌آید تا در سامانه یک شکل باشد.
    """
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return to_persian_digits(0)
    days = seconds / SECONDS_PER_DAY
    label = f"{days:.1f}".rstrip("0").rstrip(".")
    return to_persian_digits(label).replace(".", PERSIAN_DECIMAL_SEPARATOR)


def jalali_month_range(jy: int, jm: int) -> tuple[date, date]:
    """بازهٔ میلادی یک ماه جلالی (شامل روز اول و آخر)."""
    first = jalali_to_gregorian(jy, jm, 1)
    return first, jalali_to_gregorian(jy, jm, month_length(jy, jm))


def today_jalali() -> tuple[int, int, int]:
    """تاریخ امروز به جلالی."""
    from django.utils import timezone

    return gregorian_to_jalali(timezone.localdate())
