"""بازهٔ گزارش — تفسیر، اعتبارسنجی و برچسب‌گذاری شمسی.

قاعدهٔ طراحی: بازه **روزهای کامل محلی** است (نه لحظه). مدیر می‌گوید «از اول
مهر تا امروز»، نه «از ۰۰:۰۰:۰۰٫۳۱۴ UTC». پس همه‌چیز روی ``date`` بنا می‌شود و
تبدیل به بازهٔ لحظه‌ای فقط یک‌بار و در مرز (نیمه‌شب محلی) انجام می‌گیرد.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.errors import ValidationError as DomainValidationError
from apps.core.jalali import format_jalali, gregorian_to_jalali

DEFAULT_SPAN_DAYS = 30
MAX_SPAN_DAYS = 366


@dataclass(frozen=True, slots=True)
class ReportRange:
    """بازهٔ گزارش: دو سر بسته + برچسب‌های آمادهٔ نمایش."""

    start_day: date
    end_day: date
    label_override: str = ""

    @property
    def days(self) -> int:
        """تعداد روزهای بازه (شمولive)."""
        return (self.end_day - self.start_day).days + 1

    @property
    def start_moment(self) -> datetime:
        """نیمه‌شب محلی آغاز بازه."""
        return _start_of_day(self.start_day)

    @property
    def end_moment(self) -> datetime:
        """نیمه‌شب محلی پس از پایان بازه (مرز باز، مطابق قرارداد بازه‌ها)."""
        return _start_of_day(self.end_day + timedelta(days=1))

    @property
    def start_jalali(self) -> str:
        """تاریخ شمسی آغاز — عددی و بدون ابهام."""
        return format_jalali(self.start_day, numeric=True)

    @property
    def end_jalali(self) -> str:
        """تاریخ شمسی پایان."""
        return format_jalali(self.end_day, numeric=True)

    @property
    def label(self) -> str:
        """برچسب فارسی بازه برای عنوان سند."""
        return self.label_override or f"{self.start_jalali} تا {self.end_jalali}"

    @property
    def slug(self) -> str:
        """برچسب لاتین بازه برای نام فایل (بدون نویسهٔ غیرASCII)."""
        start_year, start_month, start_day = gregorian_to_jalali(self.start_day)
        end_year, end_month, end_day = gregorian_to_jalali(self.end_day)
        return (
            f"{start_year:04d}-{start_month:02d}-{start_day:02d}"
            f"_{end_year:04d}-{end_month:02d}-{end_day:02d}"
        )


def _start_of_day(day: date) -> datetime:
    """نیمه‌شب محلی یک روز به‌صورت لحظهٔ آگاه از منطقهٔ زمانی."""
    naive = datetime.combine(day, datetime.min.time())
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def parse_range(
    *,
    from_raw: str | None = None,
    to_raw: str | None = None,
    today: date | None = None,
) -> ReportRange:
    """تفسیر بازهٔ درخواستی با پیش‌فرض‌های منطقی و پیام خطای فارسی.

    پیش‌فرض‌ها: تا = امروز، از = ۲۹ روز پیش (یک ماه شمسی تقریبی). بازهٔ معکوس
    به‌جای خطا **مرتب** می‌شود (سخاوت در ورودی، سخت‌گیری در خروجی)، ولی بازهٔ
    بسیار بلند رد می‌شود تا سرور با یک درخواست، ماه‌ها داده نسازد.
    """
    today = today or timezone.localdate()
    start_day = (
        _parse_day(from_raw, field=_("از تاریخ"))
        if from_raw
        else today - timedelta(days=DEFAULT_SPAN_DAYS - 1)
    )
    end_day = _parse_day(to_raw, field=_("تا تاریخ")) if to_raw else today

    if end_day < start_day:
        start_day, end_day = end_day, start_day

    span = (end_day - start_day).days + 1
    if span > MAX_SPAN_DAYS:
        raise DomainValidationError(
            _("بازهٔ گزارش نمی‌تواند بیش از %(max)s روز باشد؛ بازهٔ شما %(span)s روز است.")
            % {"max": MAX_SPAN_DAYS, "span": span},
            code="range_too_long",
        )
    return ReportRange(start_day=start_day, end_day=end_day)


def _parse_day(raw: str, *, field: object) -> date:
    """تفسیر یک تاریخ ورودی (YYYY-MM-DD) با خطای دامنه‌ای خوانا."""
    try:
        return date.fromisoformat(raw.strip())
    except (ValueError, AttributeError) as exc:
        raise DomainValidationError(
            _("%(field)s درست نیست؛ قالب مورد انتظار YYYY-MM-DD است.") % {"field": field},
            code="invalid_date",
        ) from exc
