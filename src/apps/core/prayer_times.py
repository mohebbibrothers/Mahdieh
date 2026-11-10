"""اوقات شرعی به افق مشهد مقدس — محاسبهٔ نجومی، بی‌وابستگی و بی‌نیاز از سرویس بیرونی.

چرا محاسبه و نه جدول یا سرویس بیرونی؟
    • میزبان cPanel است و به سرویس بیرونی تکیه نمی‌کنیم؛ تقویم و راهنما باید در قطعی
      اینترنت بین‌الملل هم کار کنند.
    • اوقات شرعی تابعی قطعی از تاریخ و مختصات است؛ جدول دستی کهنه می‌شود و غلط تایپی می‌گیرد.

روش: همان روش رسمی ایران (مؤسسهٔ ژئوفیزیک دانشگاه تهران) —
    • اذان صبح: خورشید ۱۷٫۷ درجه زیر افق (پیش از طلوع)؛
    • طلوع و غروب: لبهٔ بالای قرص خورشید با شکست جوّ (۰٫۸۳۳ درجه)، بی‌اصلاح ارتفاع؛
    • اذان ظهر: عبور خورشید از نصف‌النهار؛
    • اذان مغرب: خورشید ۴٫۵ درجه زیر افق (پس از غروب، با ذهاب حمرهٔ مشرقیه)؛
    • نیمه‌شب شرعی: میانهٔ غروب آفتاب تا اذان صبح فردا.
موقعیت خورشید با الگوریتم کم‌خطای «سالنامهٔ نجومی» (دقت چند ثانیه در این قرن) و سه دور
پالایش محاسبه می‌شود. آزمون ``tests/test_prayer_times.py`` خروجی را با جدول منتشرشدهٔ رسمی
مشهد (شهرآرانیوز، تابستان و پاییز ۱۴۰۵) می‌سنجد: اختلاف زیر یک دقیقه.

ساعت ایران از ۱۴۰۱ تغییر فصلی ندارد؛ با این حال منطقهٔ زمانی از ``zoneinfo`` خوانده می‌شود
تا اگر روزی قانون عوض شد، فقط دادهٔ منطقهٔ زمانی به‌روز شود و نه این کد.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
from typing import Final
from zoneinfo import ZoneInfo

#: مختصات حرم مطهر رضوی (افق مشهد مقدس).
LATITUDE: Final[float] = 36.2880
LONGITUDE: Final[float] = 59.6158
ZONE: Final[ZoneInfo] = ZoneInfo("Asia/Tehran")

FAJR_ANGLE: Final[float] = 17.7
MAGHRIB_ANGLE: Final[float] = 4.5
RISE_SET_ANGLE: Final[float] = 0.833
REFINE_ROUNDS: Final[int] = 3
J2000: Final[float] = 2451545.0
#: ماه‌های ژانویه و فوریه در الگوریتم روز ژولیانی، ماه ۱۳ و ۱۴ سال قبل‌اند.
FEBRUARY: Final[int] = 2

METHOD_LABEL: Final[str] = "روش مؤسسهٔ ژئوفیزیک دانشگاه تهران"
CITY_LABEL: Final[str] = "مشهد مقدس"

#: کلید و برچسب فارسی هر وقت، به ترتیب روز.
SLOTS: Final[tuple[tuple[str, str], ...]] = (
    ("fajr", "اذان صبح"),
    ("sunrise", "طلوع آفتاب"),
    ("dhuhr", "اذان ظهر"),
    ("sunset", "غروب آفتاب"),
    ("maghrib", "اذان مغرب"),
    ("midnight", "نیمه‌شب شرعی"),
)


@dataclass(frozen=True, slots=True)
class PrayerDay:
    """اوقات یک روز (همه با منطقهٔ زمانی تهران و گردشده به نزدیک‌ترین دقیقه)."""

    day: date
    fajr: datetime
    sunrise: datetime
    dhuhr: datetime
    sunset: datetime
    maghrib: datetime
    midnight: datetime

    def slots(self) -> list[tuple[str, str, datetime]]:
        """(کلید، برچسب، زمان) به ترتیب روز — برای نمایش و API."""
        return [(key, label, getattr(self, key)) for key, label in SLOTS]


def _sin(degrees: float) -> float:
    return math.sin(math.radians(degrees))


def _cos(degrees: float) -> float:
    return math.cos(math.radians(degrees))


def _wrap(value: float, base: float) -> float:
    return value - base * math.floor(value / base)


def _julian(day: date) -> float:
    """روز ژولیانی نیمه‌شب گرینویچ یک تاریخ میلادی."""
    year, month = day.year, day.month
    if month <= FEBRUARY:
        year -= 1
        month += 12
    century = year // 100
    gregorian = 2 - century + century // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day.day
        + gregorian
        - 1524.5
    )


def _sun(jd: float) -> tuple[float, float]:
    """(میل خورشید به درجه، معادلهٔ زمان به ساعت) در روز ژولیانی ``jd``."""
    days = jd - J2000
    anomaly = _wrap(357.529 + 0.98560028 * days, 360)
    mean_longitude = _wrap(280.459 + 0.98564736 * days, 360)
    longitude = _wrap(mean_longitude + 1.915 * _sin(anomaly) + 0.020 * _sin(2 * anomaly), 360)
    obliquity = 23.439 - 0.00000036 * days
    ascension = math.degrees(math.atan2(_cos(obliquity) * _sin(longitude), _cos(longitude))) / 15
    equation = mean_longitude / 15 - _wrap(ascension, 24)
    declination = math.degrees(math.asin(_sin(obliquity) * _sin(longitude)))
    return declination, equation


def _hours(day: date) -> dict[str, float]:
    """اوقات خام یک روز به «ساعت محلی میانگین» (پیش از منطقهٔ زمانی)."""
    jd = _julian(day) - LONGITUDE / (15 * 24)

    def noon(fraction: float) -> float:
        """عبور خورشید از نصف‌النهار (ساعت محلی میانگین)."""
        return _wrap(12 - _sun(jd + fraction)[1], 24)

    def angle_time(angle: float, fraction: float, *, before_noon: bool) -> float:
        """لحظه‌ای که خورشید ``angle`` درجه زیر افق است (پیش یا پس از ظهر)."""
        declination = _sun(jd + fraction)[0]
        ratio = (-_sin(angle) - _sin(declination) * _sin(LATITUDE)) / (
            _cos(declination) * _cos(LATITUDE)
        )
        # مشهد هرگز «روز قطبی» ندارد؛ این برش فقط ضد خطای ممیز شناور است.
        span = math.degrees(math.acos(max(-1.0, min(1.0, ratio)))) / 15
        return noon(fraction) + (-span if before_noon else span)

    guess = {"fajr": 5.0, "sunrise": 6.0, "dhuhr": 12.0, "sunset": 18.0, "maghrib": 18.0}
    for _ in range(REFINE_ROUNDS):
        part = {key: value / 24 for key, value in guess.items()}
        guess = {
            "fajr": angle_time(FAJR_ANGLE, part["fajr"], before_noon=True),
            "sunrise": angle_time(RISE_SET_ANGLE, part["sunrise"], before_noon=True),
            "dhuhr": noon(part["dhuhr"]),
            "sunset": angle_time(RISE_SET_ANGLE, part["sunset"], before_noon=False),
            "maghrib": angle_time(MAGHRIB_ANGLE, part["maghrib"], before_noon=False),
        }
    return guess


def _at(day: date, mean_hours: float) -> datetime:
    """ساعت محلی میانگین ⇒ زمان آگاه از منطقه (گردشده به نزدیک‌ترین دقیقه)."""
    midnight = datetime.combine(day, time(), tzinfo=ZONE)
    offset = midnight.utcoffset() or timedelta()
    clock = mean_hours + offset.total_seconds() / 3600 - LONGITUDE / 15
    minutes = round(clock * 60)
    return midnight + timedelta(minutes=minutes)


def for_day(day: date) -> PrayerDay:
    """اوقات شرعی یک روز به افق مشهد."""
    raw = _hours(day)
    fajr_next = _at(day + timedelta(days=1), _hours(day + timedelta(days=1))["fajr"])
    sunset = _at(day, raw["sunset"])
    return PrayerDay(
        day=day,
        fajr=_at(day, raw["fajr"]),
        sunrise=_at(day, raw["sunrise"]),
        dhuhr=_at(day, raw["dhuhr"]),
        sunset=sunset,
        maghrib=_at(day, raw["maghrib"]),
        midnight=sunset + timedelta(minutes=round((fajr_next - sunset).total_seconds() / 120)),
    )


def for_range(start: date, days: int) -> list[PrayerDay]:
    """اوقات چند روز پیاپی از ``start``."""
    return [for_day(start + timedelta(days=offset)) for offset in range(days)]
