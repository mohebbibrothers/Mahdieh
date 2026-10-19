"""بازهٔ زمانی نیم‌باز ``[آغاز، پایان)`` — مقدار دامنه و شرط هم‌پوشانی کوئری.

پیش‌تر بازه‌ها ستون ``tstzrange`` پستگرس بودند. روی MariaDB (ADR-0020) هر بازه
دو ستون صریح است (``starts_at`` / ``ends_at``) و این ماژول دو چیز را یک‌جا و
یک‌شکل نگه می‌دارد:

۱. ``Period``: مقدار تغییرناپذیر با همان نام‌های ``lower``/``upper`` که قالب‌ها،
   سریالایزر بازه (``PeriodRangeField``) و گزارش‌ها از قبل می‌شناختند.
۲. ``overlap_q``: شرط هم‌پوشانی نیم‌باز — **همان** معنای عملگر ``&&`` پستگرس:
   دو بازه هم‌پوشان‌اند اگر و تنها اگر ``a.start < b.end`` و ``b.start < a.end``.
   پس بازه‌های پشت‌سرهم (پایان یکی = آغاز بعدی) هم‌پوشان **نیستند**؛ همان
   قاعده‌ای که تریگر پایگاه‌داده هم اجرا می‌کند.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q


@dataclass(frozen=True, slots=True)
class Period:
    """بازهٔ نیم‌باز ``[lower, upper)``؛ مرز ``None`` یعنی بی‌کران از آن سو."""

    lower: datetime | None
    upper: datetime | None

    @property
    def isempty(self) -> bool:
        """بازهٔ تهی: هر دو مرز معلوم و آغاز دیرتر یا برابر پایان."""
        return self.lower is not None and self.upper is not None and self.lower >= self.upper

    def overlaps(self, start: datetime | None, end: datetime | None) -> bool:
        """هم‌پوشانی نیم‌باز با بازهٔ دیگر (مرز ``None`` = بی‌کران)."""
        if self.isempty or (start is not None and end is not None and start >= end):
            return False
        before_other_end = end is None or self.lower is None or self.lower < end
        after_other_start = start is None or self.upper is None or start < self.upper
        return before_other_end and after_other_start


def overlap_q(start_field: str, end_field: str, start: datetime | None, end: datetime | None) -> Q:
    """شرط کوئری «بازهٔ ستون‌ها با ``[start, end)`` هم‌پوشان است».

    مرز ``None`` یعنی بی‌کران؛ مثلاً ``overlap_q(..., moment, None)`` همهٔ
    بازه‌هایی را می‌دهد که پس از ``moment`` هنوز تمام نشده‌اند.
    """
    condition = Q()
    if end is not None:
        condition &= Q(**{f"{start_field}__lt": end})
    if start is not None:
        condition &= Q(**{f"{end_field}__gt": start})
    return condition
