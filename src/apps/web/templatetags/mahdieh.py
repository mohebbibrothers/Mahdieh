"""فیلترهای قالب — «فارسی‌سازی» و نشانه‌گذاری وضعیت‌ها در یک نقطه.

قاعدهٔ طراحی: هیچ قالبی عدد انگلیسی یا نام وضعیت خام چاپ نمی‌کند. القای
یکنواختی («همه‌جا فارسی، همه‌جا یک رنگ برای یک وضعیت») تنها با یک منبع
حقیقت ممکن است؛ این ماژول همان است.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Final

from django import template
from django.utils import timezone

from apps.core.jalali import format_jalali, weekday_short
from apps.inventory.lattice import stay_label
from apps.web.presenters import PERSIAN_DIGITS, fa

register = template.Library()


def _local(value: Any) -> Any:
    """تبدیل زمان آگاه به منطقهٔ محلی سایت — یک نقطه برای کل لایهٔ نمایش.

    مدل‌ها زمان را UTC نگه می‌دارند (``USE_TZ=True``)؛ هر نمایش ساعت باید از
    این تابع بگذرد، وگرنه کاربر ۳:۳۰ ساعت اختلاف می‌بیند. اشیای تاریخ/ساعت
    ساده (``date``/``time``) دست‌نخورده بازگردانده می‌شوند.
    """
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


# نگاشت وضعیت سلول/رزرو ⇒ لحن رنگی رابط کاربری
TONES: Final[dict[str, str]] = {
    "free": "tone-free",
    "occupied": "tone-busy",
    "buffer": "tone-warn",
    "blocked": "tone-lock",
    "past": "tone-past",
    "hold": "tone-hold",
    "confirmed": "tone-ok",
    "checked_in": "tone-live",
    "completed": "tone-done",
    "cancelled_by_guest": "tone-off",
    "cancelled_by_staff": "tone-off",
    "no_show": "tone-off",
    "expired": "tone-off",
    "superseded": "tone-off",
}

# وضعیت‌های رزرو ⇒ برچسب فارسی
STATUS_LABELS: Final[dict[str, str]] = {
    "hold": "نگه‌داشت موقت",
    "confirmed": "تأییدشده",
    "checked_in": "پذیرش‌شده",
    "completed": "پایان‌یافته",
    "cancelled_by_guest": "لغو توسط زائر",
    "cancelled_by_staff": "لغو توسط کارکنان",
    "no_show": "عدم‌حضور",
    "expired": "منقضی",
    "superseded": "جانشین‌شده",
}

CELL_STATUS_LABELS: Final[dict[str, str]] = {
    "free": "آزاد",
    "occupied": "اشغال",
    "buffer": "بافر نظافت",
    "blocked": "بسته",
    "past": "گذشته",
}


@register.filter(name="fa")
def fa_filter(value: Any) -> str:
    """تبدیل ارقام لاتین به ارقام فارسی (نمایش، نه محاسبه).

    قاعدهٔ پروژه: قالب هرگز عدد لاتین چاپ نمی‌کند. اتکا به ویژگی ``locl`` فونت
    کافی نیست — کپی/پیست، صفحه‌خوان و خروجی چاپی همه باید فارسی ببینند.
    پیاده‌سازی در ``apps.web.presenters.fa`` است تا نماها و قالب‌ها یک رفتار
    داشته باشند.
    """
    return fa(value)


@register.filter(name="tone")
def tone(value: Any) -> str:
    """کلاس لحن رنگی برای یک وضعیت."""
    return TONES.get(str(value), "tone-past")


@register.filter(name="status_label")
def status_label(value: Any) -> str:
    """برچسب فارسی وضعیت رزرو یا سلول تقویم."""
    key = str(value)
    return STATUS_LABELS.get(key) or CELL_STATUS_LABELS.get(key) or key


@register.filter(name="jalali")
def jalali(value: date | datetime | None, style: str = "long") -> str:
    """تاریخ جلالی؛ ``style`` می‌تواند ``short`` (عدد فارسی) یا ``long`` باشد.

    ورودی نامعتبر (مقدار نبودِ قالب یا رشته) به «—» نگاشت می‌شود تا قالب هرگز
    با ۵۰۰ سرور پاسخ ندهد — رفتار قالب باید قابل پیش‌بینی باشد.
    """
    if not isinstance(value, (date, datetime)):
        return "—"
    value = _local(value)
    if style == "short":
        return fa(format_jalali(value, numeric=True))
    if style == "weekday":
        return fa(format_jalali(value, with_weekday=True))
    return fa(format_jalali(value))


@register.filter(name="weekday")
def weekday(value: date | datetime) -> str:
    """حرف نشانهٔ روز هفته."""
    return weekday_short(_local(value))


@register.filter(name="clock")
def clock(value: datetime | time | None) -> str:
    """ساعت محلی به قالب ``HH:MM`` — بدون ثانیه، با ارقام فارسی."""
    if value is None:
        return "—"
    local = _local(value)
    return f"{local.hour:02d}:{local.minute:02d}".translate(PERSIAN_DIGITS)


@register.filter(name="duration")
def duration(cells: int | None) -> str:
    """برچسب مدت اقامت بر پایهٔ تعداد نیم‌روز (یک منبع حقیقت: lattice)."""
    if not cells:
        return "—"
    return stay_label(int(cells))


@register.filter(name="percent")
def percent(value: float | int | None, digits: int = 0) -> str:
    """درصد با ارقام فارسی — ورودی نرخ (۰ تا ۱) یا درصد."""
    if value is None:
        return "۰٪"
    number = float(value)
    if number <= 1:
        number *= 100
    return fa(f"{number:.{digits}f}") + "٪"


@register.filter(name="range_label")
def range_label(value: Any, sep: str = " → ") -> str:
    """برچسب بازهٔ زمانی با ارقام فارسی؛ ورودی یک ``Range`` یا داتایم."""
    if value is None:
        return "—"
    lower = getattr(value, "lower", None)
    upper = getattr(value, "upper", None)
    if lower is None or upper is None:
        return "—"
    return f"{fa(format_jalali(_local(lower)))} {clock(lower)}{sep}{clock(upper)}"


@register.simple_tag(name="cell_tone")
def cell_tone(status: str) -> str:
    """تَگ ساده برای استفاده در حلقه‌ها: کلاس لحن یک وضعیت سلول."""
    return TONES.get(status, "tone-past")


@register.filter(name="cell_hint")
def cell_hint(cell: Any) -> str:
    """راهنمای سلول تقویم (متن tooltip) — یک منبع، در تقویم و صفحهٔ واحد."""
    from apps.web import presenters

    return presenters.cell_hint(cell)


@register.filter(name="get")
def get(mapping: Any, key: Any) -> Any:
    """خواندن مقدار یک نگاشت با کلید متغیر (جایگزین ایندکس متغیر در قالب)."""
    if hasattr(mapping, "get"):
        return mapping.get(key, "")
    return ""


@register.filter(name="mask_phone")
def mask_phone(value: Any) -> str:
    """پوشاندن شمارهٔ تلفن در نمایش عمومی — سیاست حفظ حریم خصوصی."""
    from apps.identity.validators import mask_phone as mask

    return mask(str(value or ""))
