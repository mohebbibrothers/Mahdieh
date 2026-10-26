"""تقویم رسمی ایران — تعطیلات رسمی و مناسبت‌های مذهبی، تبدیل قمری به میلادی (تابعی و بی‌وابستگی).

مرجع قانونی تعطیلات و تاریخ‌های قمری در ایران، «تقویم رسمی کشور» است که مرکز تقویم مؤسسهٔ
ژئوفیزیک دانشگاه تهران هر سال منتشر و شورای فرهنگ عمومی تصویب می‌کند. ماه قمری در ایران بر پایهٔ
رؤیت هلال آغاز می‌شود و با هیچ الگوریتم ثابتی (ام‌القری، جدولی) یک‌به‌یک نمی‌خواند؛ پس:

    • آغاز هر ماه قمری از جدول «LunarMonthStart» در پایگاه‌داده خوانده می‌شود (بذرش از تقویم رسمی
      ۱۴۰۵؛ مدیر می‌تواند با اعلام رسمی رؤیت — مثلاً عید فطر — آن را اصلاح کند).
    • برای ماه‌هایی که هنوز در تقویم رسمی نیامده‌اند، از نزدیک‌ترین ماه رسمی با میانگین ماه هلالی
      (۲۹٫۵۳۰۵۸۹ روز) برآورد می‌شود و مناسبت با «تقریبی» (``provisional``) علامت می‌خورد.
    • مناسبت‌های شمسی (نوروز، ۲۲ بهمن، …) دقیق و قطعی‌اند.

این ماژول فقط داده و تابع خالص است؛ نوشتن در پایگاه‌داده کار ``services.sync_official_occasions`` است.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from apps.core.jalali import gregorian_to_jalali, jalali_to_gregorian
from apps.core.text import to_persian_digits
from apps.inventory.choices import DemandLevel, OccasionKind

HIJRI_MONTHS: Final[tuple[str, ...]] = (
    "محرم",
    "صفر",
    "ربیع‌الاول",
    "ربیع‌الثانی",
    "جمادی‌الاول",
    "جمادی‌الثانی",
    "رجب",
    "شعبان",
    "رمضان",
    "شوال",
    "ذی‌القعده",
    "ذی‌الحجه",
)

#: میانگین ماه هلالی (روز) — فقط برای برآورد ماه‌هایی که هنوز در تقویم رسمی نیامده‌اند.
SYNODIC_MONTH: Final[float] = 29.530588853

#: «روز آخر ماه» در فهرست مناسبت‌ها (مثل شهادت امام رضا (ع) که در ایران آخر صفر است، ۲۹ یا ۳۰).
LAST_DAY: Final[int] = 0

#: آغاز رسمی ماه‌های قمری در ایران — بذر جدول ``LunarMonthStart`` (مهاجرت ۰۰۰۴).
#: استخراج و تطبیق‌شده با تقویم رسمی ۱۴۰۵ (مؤسسهٔ ژئوفیزیک دانشگاه تهران): هر ۲۶ تعطیل رسمی
#: ۱۴۰۵ و مناسبت‌های غیرتعطیل (روز پرستار ۲۴ مهر، وفات حضرت معصومه ۳۱ شهریور، ولادت امام رضا
#: ۹ اردیبهشت) دقیقاً از همین جدول بازتولید می‌شوند (آزمون ``test_official_calendar``).
OFFICIAL_MONTH_STARTS_1405: Final[tuple[tuple[int, int, date], ...]] = (
    (1447, 9, date(2026, 2, 19)),
    (1447, 10, date(2026, 3, 21)),
    (1447, 11, date(2026, 4, 19)),
    (1447, 12, date(2026, 5, 18)),
    (1448, 1, date(2026, 6, 16)),
    (1448, 2, date(2026, 7, 16)),
    (1448, 3, date(2026, 8, 14)),
    (1448, 4, date(2026, 9, 13)),
    (1448, 5, date(2026, 10, 12)),
    (1448, 6, date(2026, 11, 11)),
    (1448, 7, date(2026, 12, 11)),
    (1448, 8, date(2027, 1, 10)),
    (1448, 9, date(2027, 2, 8)),
    (1448, 10, date(2027, 3, 10)),
)

MonthStarts = Mapping[tuple[int, int], date]

P, H, N = DemandLevel.PEAK, DemandLevel.HIGH, DemandLevel.NORMAL
REL, HOL = OccasionKind.RELIGIOUS, OccasionKind.HOLIDAY


@dataclass(frozen=True, slots=True)
class OfficialEvent:
    """یک مناسبت تقویم رسمی: تاریخ شمسی («j») یا قمری («h») + نوع، تعطیلی و سطح تقاضا در مشهد."""

    slug: str
    title: str
    calendar: str
    month: int
    day: int
    kind: str
    holiday: bool
    demand: str


# ── فهرست مناسبت‌ها ───────────────────────────────────────────────────────────────────────────
# سطح تقاضا از نگاه زائرسرای مشهد: «اوج» برای تعطیلات بزرگ مذهبی/نوروزی و مناسبت‌های رضوی؛
# «پرتقاضا» برای دیگر تعطیلات رسمی و همهٔ ولادت‌ها، شهادت‌ها و مناسبت‌های مذهبی تقویم رسمی.
EVENTS: Final[tuple[OfficialEvent, ...]] = (
    # تعطیلات رسمی شمسی
    OfficialEvent("nowruz-1", "آغاز نوروز", "j", 1, 1, HOL, True, P),
    OfficialEvent("nowruz-2", "عید نوروز", "j", 1, 2, HOL, True, P),
    OfficialEvent("nowruz-3", "عید نوروز", "j", 1, 3, HOL, True, P),
    OfficialEvent("nowruz-4", "عید نوروز", "j", 1, 4, HOL, True, P),
    OfficialEvent("republic-day", "روز جمهوری اسلامی", "j", 1, 12, HOL, True, P),
    OfficialEvent("nature-day", "روز طبیعت", "j", 1, 13, HOL, True, P),
    OfficialEvent("khomeini", "رحلت امام خمینی (ره)", "j", 3, 14, HOL, True, H),
    OfficialEvent("khordad-15", "قیام ۱۵ خرداد", "j", 3, 15, HOL, True, H),
    OfficialEvent("revolution", "پیروزی انقلاب اسلامی", "j", 11, 22, HOL, True, H),
    OfficialEvent("oil-day", "ملی شدن صنعت نفت", "j", 12, 29, HOL, True, H),
    # محرم
    OfficialEvent("tasua", "تاسوعای حسینی", "h", 1, 9, REL, True, P),
    OfficialEvent("ashura", "عاشورای حسینی", "h", 1, 10, REL, True, P),
    OfficialEvent("sajjad-martyr", "شهادت امام سجاد (ع)", "h", 1, 25, REL, False, H),
    # صفر
    OfficialEvent("arbaeen", "اربعین حسینی", "h", 2, 20, REL, True, P),
    OfficialEvent(
        "prophet-demise", "رحلت پیامبر (ص) و شهادت امام حسن (ع)", "h", 2, 28, REL, True, P
    ),
    OfficialEvent("reza-martyr", "شهادت امام رضا (ع)", "h", 2, LAST_DAY, REL, True, P),
    # ربیع‌الاول
    OfficialEvent("askari-martyr", "شهادت امام حسن عسکری (ع)", "h", 3, 8, REL, True, H),
    OfficialEvent("mahdi-imamate", "آغاز امامت حضرت ولی‌عصر (عج)", "h", 3, 9, REL, False, H),
    OfficialEvent(
        "unity-week", "میلاد پیامبر (ص) به روایت اهل سنت — هفتهٔ وحدت", "h", 3, 12, REL, False, H
    ),
    OfficialEvent("prophet-birth", "میلاد پیامبر (ص) و امام صادق (ع)", "h", 3, 17, REL, True, H),
    # ربیع‌الثانی
    OfficialEvent("askari-birth", "ولادت امام حسن عسکری (ع)", "h", 4, 8, REL, False, H),
    OfficialEvent("masumeh-demise", "وفات حضرت معصومه (س)", "h", 4, 10, REL, False, H),
    # جمادی‌الاول و جمادی‌الثانی
    OfficialEvent("zaynab-birth", "ولادت حضرت زینب (س)", "h", 5, 5, REL, False, H),
    OfficialEvent(
        "fatima-martyr-1", "شهادت حضرت فاطمه (س) — روایت نخست", "h", 5, 13, REL, False, H
    ),
    OfficialEvent("fatima-martyr", "شهادت حضرت فاطمهٔ زهرا (س)", "h", 6, 3, REL, True, H),
    OfficialEvent("fatima-birth", "ولادت حضرت فاطمه (س) — روز مادر", "h", 6, 20, REL, False, H),
    # رجب
    OfficialEvent("baqir-birth", "ولادت امام محمدباقر (ع)", "h", 7, 1, REL, False, H),
    OfficialEvent("hadi-martyr", "شهادت امام علی‌النقی (ع)", "h", 7, 3, REL, False, H),
    OfficialEvent("jawad-birth", "ولادت امام محمدتقی (ع)", "h", 7, 10, REL, False, H),
    OfficialEvent("ali-birth", "ولادت امام علی (ع) — روز پدر", "h", 7, 13, REL, True, H),
    OfficialEvent("zaynab-demise", "وفات حضرت زینب (س)", "h", 7, 15, REL, False, H),
    OfficialEvent("kazim-martyr", "شهادت امام موسی کاظم (ع)", "h", 7, 25, REL, False, H),
    OfficialEvent("mabath", "مبعث پیامبر (ص)", "h", 7, 27, REL, True, H),
    # شعبان
    OfficialEvent("husayn-birth", "ولادت امام حسین (ع)", "h", 8, 3, REL, False, H),
    OfficialEvent("abbas-birth", "ولادت حضرت ابوالفضل (ع)", "h", 8, 4, REL, False, H),
    OfficialEvent("sajjad-birth", "ولادت امام سجاد (ع)", "h", 8, 5, REL, False, H),
    OfficialEvent("akbar-birth", "ولادت حضرت علی‌اکبر (ع)", "h", 8, 11, REL, False, H),
    OfficialEvent("mahdi-birth", "ولادت حضرت قائم (عج) — نیمهٔ شعبان", "h", 8, 15, REL, True, P),
    # رمضان (شب قدر = شبِ پیش از روز؛ سانس شبانهٔ روز ۱۸، ۲۰ و ۲۲)
    OfficialEvent("khadija-demise", "وفات حضرت خدیجه (س)", "h", 9, 10, REL, False, H),
    OfficialEvent("hasan-birth", "ولادت امام حسن مجتبی (ع)", "h", 9, 15, REL, False, H),
    OfficialEvent("qadr-19", "شب قدر — شب نوزدهم", "h", 9, 18, REL, False, H),
    OfficialEvent("ali-struck", "ضربت خوردن امام علی (ع)", "h", 9, 19, REL, False, H),
    OfficialEvent("qadr-21", "شب قدر — شب بیست‌ویکم", "h", 9, 20, REL, False, H),
    OfficialEvent("ali-martyr", "شهادت امام علی (ع)", "h", 9, 21, REL, True, H),
    OfficialEvent("qadr-23", "شب قدر — شب بیست‌وسوم", "h", 9, 22, REL, False, H),
    # شوال
    OfficialEvent("fitr", "عید سعید فطر", "h", 10, 1, REL, True, P),
    OfficialEvent("fitr-2", "تعطیل به مناسبت عید فطر", "h", 10, 2, REL, True, P),
    OfficialEvent("sadiq-martyr", "شهادت امام جعفر صادق (ع)", "h", 10, 25, REL, True, H),
    # ذی‌القعده — دههٔ کرامت
    OfficialEvent(
        "masumeh-birth", "ولادت حضرت معصومه (س) — آغاز دههٔ کرامت", "h", 11, 1, REL, False, H
    ),
    OfficialEvent("reza-birth", "ولادت امام رضا (ع)", "h", 11, 11, REL, False, P),
    OfficialEvent("reza-ziyarat", "روز زیارتی مخصوص امام رضا (ع)", "h", 11, 23, REL, False, P),
    OfficialEvent("dahw-al-ard", "دحوالارض", "h", 11, 25, REL, False, H),
    OfficialEvent("jawad-martyr", "شهادت امام محمدتقی (ع)", "h", 11, 29, REL, False, H),
    # ذی‌الحجه
    OfficialEvent(
        "ali-fatima-marriage",
        "سالروز ازدواج امام علی (ع) و حضرت فاطمه (س)",
        "h",
        12,
        1,
        REL,
        False,
        H,
    ),
    OfficialEvent("baqir-martyr", "شهادت امام محمدباقر (ع)", "h", 12, 7, REL, False, H),
    OfficialEvent("arafah", "روز عرفه", "h", 12, 9, REL, False, P),
    OfficialEvent("adha", "عید سعید قربان", "h", 12, 10, REL, True, P),
    OfficialEvent("hadi-birth", "ولادت امام علی‌النقی (ع)", "h", 12, 15, REL, False, H),
    OfficialEvent("ghadir", "عید سعید غدیر خم", "h", 12, 18, REL, True, P),
    OfficialEvent("kazim-birth", "ولادت امام موسی کاظم (ع)", "h", 12, 20, REL, False, H),
    OfficialEvent("mubahala", "روز مباهله", "h", 12, 24, REL, False, H),
)

DESCRIPTIONS: Final[dict[str, str]] = {
    P: (
        "از پرتقاضاترین روزهای زیارتی مشهد؛ زودتر برنامه‌ریزی کنید و اگر جایی نماند، "
        "در صف انتظار بمانید."
    ),
    H: "روزی پرتقاضا در تقویم زیارتی؛ ظرفیت زودتر از روزهای عادی پر می‌شود.",
    N: "",
}


@dataclass(frozen=True, slots=True)
class GeneratedOccasion:
    """یک نمونهٔ تاریخ‌دار از مناسبت رسمی — ورودی همگام‌سازی پایگاه‌داده."""

    key: str
    event: OfficialEvent
    day: date
    hijri_label: str
    provisional: bool


MONTHS_PER_YEAR: Final[int] = 12


def next_month(year: int, month: int) -> tuple[int, int]:
    """ماه قمری بعدی (ذی‌الحجه ⇒ محرم سال بعد)."""
    return (year + 1, 1) if month == MONTHS_PER_YEAR else (year, month + 1)


def _index(year: int, month: int) -> int:
    return year * MONTHS_PER_YEAR + (month - 1)


def month_start(year: int, month: int, starts: MonthStarts) -> tuple[date, bool]:
    """آغاز ماه قمری ⇒ (تاریخ میلادی، رسمی‌بودن). نبود در جدول ⇒ برآورد از نزدیک‌ترین ماه رسمی."""
    known = starts.get((year, month))
    if known is not None:
        return known, True
    if not starts:
        msg = "جدول آغاز ماه‌های قمری خالی است"
        raise ValueError(msg)
    target = _index(year, month)
    anchor_key = min(starts, key=lambda k: abs(_index(*k) - target))
    delta = target - _index(*anchor_key)
    return starts[anchor_key] + timedelta(days=round(delta * SYNODIC_MONTH)), False


def hijri_to_gregorian(year: int, month: int, day: int, starts: MonthStarts) -> tuple[date, bool]:
    """روز قمری ⇒ (تاریخ میلادی، رسمی‌بودن). ``day=LAST_DAY`` یعنی آخرین روز همان ماه."""
    start, official = month_start(year, month, starts)
    if day != LAST_DAY:
        return start + timedelta(days=day - 1), official
    nxt, nxt_official = month_start(*next_month(year, month), starts)
    return nxt - timedelta(days=1), official and nxt_official


def hijri_label(year: int, month: int, day: int) -> str:
    """برچسب فارسی تاریخ قمری، مثل «۳ جمادی‌الثانی ۱۴۴۸»."""
    return to_persian_digits(f"{day} {HIJRI_MONTHS[month - 1]} {year}")


def _hijri_years(start: date, end: date, starts: MonthStarts) -> range:
    """سال‌های قمری‌ای که بازهٔ میلادی را می‌پوشانند (با یک سال حاشیه در هر سو)."""
    anchor_key = min(starts)
    anchor = starts[anchor_key]

    def year_at(value: date) -> int:
        """سال هجری تقریبی یک تاریخ میلادی بر پایهٔ میانگین ماه هلالی از لنگر رسمی."""
        months = (value - anchor).days / SYNODIC_MONTH
        return (_index(*anchor_key) + int(months)) // MONTHS_PER_YEAR

    return range(year_at(start) - 1, year_at(end) + 2)


def generate(start: date, end: date, starts: MonthStarts) -> Iterator[GeneratedOccasion]:
    """همهٔ مناسبت‌های رسمی بازهٔ [start, end] (هر دو سر) — قطعی و بی‌اثر جانبی."""
    jy_first, jy_last = gregorian_to_jalali(start)[0], gregorian_to_jalali(end)[0]
    for event in EVENTS:
        if event.calendar == "j":
            for jy in range(jy_first, jy_last + 1):
                day = jalali_to_gregorian(jy, event.month, event.day)
                if start <= day <= end:
                    hy, hm, hd = _hijri_of(day, starts)
                    yield GeneratedOccasion(
                        key=f"ir:{event.slug}:{jy}",
                        event=event,
                        day=day,
                        hijri_label=hijri_label(hy, hm, hd),
                        provisional=False,
                    )
            continue
        for hy in _hijri_years(start, end, starts):
            day, official = hijri_to_gregorian(hy, event.month, event.day, starts)
            if start <= day <= end:
                real_day = event.day or (day - month_start(hy, event.month, starts)[0]).days + 1
                yield GeneratedOccasion(
                    key=f"ir:{event.slug}:{hy}",
                    event=event,
                    day=day,
                    hijri_label=hijri_label(hy, event.month, real_day),
                    provisional=not official,
                )


def _hijri_of(value: date, starts: MonthStarts) -> tuple[int, int, int]:
    """تاریخ میلادی ⇒ (سال، ماه، روز) قمری بر پایهٔ همان جدول (برای برچسب مناسبت‌های شمسی)."""
    for hy in _hijri_years(value, value, starts):
        for hm in range(1, 13):
            first, _ = month_start(hy, hm, starts)
            nxt, _ = month_start(*next_month(hy, hm), starts)
            if first <= value < nxt:
                return hy, hm, (value - first).days + 1
    msg = f"تاریخ قمری {value} یافت نشد"  # pragma: no cover - جدول پیوسته است
    raise ValueError(msg)
