"""عملیات SQL درایور MariaDB — تبدیل منطقهٔ زمانی بدون جدول‌های tz سرور.

مسئله (سنجیده روی میزبان واقعی، مرحلهٔ ۳ استقرار): روی میزبان اشتراکی جدول‌های
منطقهٔ زمانی MariaDB بارگذاری نشده‌اند و ``CONVERT_TZ(x, 'UTC', 'Asia/Tehran')``
**بی‌صدا NULL** برمی‌گرداند. جنگو برای ``__date``، ``date_hierarchy`` پنل مدیریت
و ``Trunc*``/``Extract*`` دقیقاً همین را می‌فرستد ⇒ فیلتر تاریخ خالی می‌شد.

راه‌حل: به‌جای نام منطقه، **اختلاف عددی** (``+03:30``) فرستاده می‌شود که به هیچ
جدولی نیاز ندارد. این تبدیل برای منطقه‌های بدون ساعت تابستانی دقیق است؛ ایران
از ۱۴۰۱ ساعت تابستانی ندارد. نگهبان ``mahdieh.E020`` (``apps.core.checks``) اگر
روزی ``TIME_ZONE`` به منطقهٔ دارای جابه‌جایی ساعت تغییر کند، راه‌اندازی را می‌بندد.
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.backends.mysql import operations
from django.db.backends.utils import split_tzname_delta
from django.utils import timezone

MINUTES_PER_HOUR = 60


def format_offset(delta: timedelta) -> str:
    """``timedelta`` ⇒ ``±HH:MM`` به قالبی که ``CONVERT_TZ`` می‌فهمد."""
    total_minutes = int(delta.total_seconds() // 60)
    sign = "-" if total_minutes < 0 else "+"
    hours, minutes = divmod(abs(total_minutes), MINUTES_PER_HOUR)
    return f"{sign}{hours:02d}:{minutes:02d}"


@lru_cache(maxsize=32)
def numeric_offset(tzname: str) -> str:
    """نام منطقه یا اختلاف ⇒ اختلاف عددی ثابت (``UTC`` ⇒ ``+00:00``)."""
    name, sign, offset = split_tzname_delta(tzname)
    if offset:
        return f"{sign}{offset}"
    if name.upper() in {"UTC", "GMT", "Z", "ETC/UTC"}:
        return "+00:00"
    delta = timezone.now().astimezone(ZoneInfo(name)).utcoffset() or timedelta(0)
    return format_offset(delta)


class DatabaseOperations(operations.DatabaseOperations):
    """عملیات MariaDB با تبدیل منطقهٔ زمانی مستقل از جدول‌های tz سرور."""

    def _convert_sql_to_tz(
        self, sql: str, params: Any, tzname: str | None
    ) -> tuple[str, tuple[Any, ...]]:
        """تبدیل ستون از UTC به منطقهٔ جاری با اختلاف عددی (نه نام منطقه)."""
        if tzname and settings.USE_TZ and self.connection.timezone_name != tzname:
            return f"CONVERT_TZ({sql}, %s, %s)", (
                *params,
                numeric_offset(self.connection.timezone_name),
                numeric_offset(tzname),
            )
        return sql, tuple(params)
