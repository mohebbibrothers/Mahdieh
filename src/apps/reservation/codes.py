"""کدهای انسان‌خوان رزرو — «M-1405-0231» به‌جای UUID در مکالمهٔ تلفنی.

چرا کد خوانا لازم است؟ کارمند پذیرش روی تلفن می‌پرسد «کد رزروتان چیست؟».
خواندن یک UUID غیرممکن است؛ خواندن «۱۴۰۵-۰۲۳۱» ساده است. کد همچنین در پیامک
و در کارت رزرو نمایش داده می‌شود.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from django.db import connections, router, transaction
from django.utils import timezone

from apps.core.db.serial import quoted_column
from apps.core.jalali import gregorian_to_jalali
from apps.reservation.models import CodeSequence

CODE_PREFIX: Final[str] = "M"
CODE_PAD: Final[int] = 4


def next_reservation_code(*, on: date | None = None) -> str:
    """تولید کد یکتای رزرو برای سال جلالی جاری.

    هم‌زمانی: یک دستور ``INSERT … ON DUPLICATE KEY UPDATE last_value = last_value + 1``
    ردیف شمارندهٔ سال را می‌سازد یا یکی بالا می‌برد و در همان لحظه قفل انحصاری آن
    را می‌گیرد؛ درخواست هم‌زمان پشت این قفل می‌ماند و کد بعدی را می‌گیرد (بدون شکاف
    و بدون تکرار). الگوی قدیمی ``get_or_create`` + ``select_for_update`` در نخستین
    رزرو سال با چند درخواست هم‌زمان به بن‌بست ۱۲۱۳ می‌رسید.

    قفل تا پایان تراکنش بیرونی (ثبت رزرو) می‌ماند؛ پس اگر رزرو برگردد، شماره هم
    برمی‌گردد و شکاف نمی‌ماند.
    """
    day = on or timezone.localdate()
    jalali_year, _month, _day = gregorian_to_jalali(day)
    scope = str(jalali_year)

    alias = router.db_for_write(CodeSequence)
    with transaction.atomic(using=alias):
        connection = connections[alias]
        table = connection.ops.quote_name(CodeSequence._meta.db_table)
        scope_col, value_col, updated_col = (
            quoted_column(connection, CodeSequence, name)
            for name in ("scope", "last_value", "updated_at")
        )
        now = connection.ops.adapt_datetimefield_value(timezone.now())
        # فقط نام‌های نقل‌شدهٔ جدول/ستون در SQL درج می‌شوند؛ مقدارها پارامترند.
        sql = (
            f"INSERT INTO {table} ({scope_col}, {value_col}, {updated_col}) "  # noqa: S608  # nosec B608
            f"VALUES (%s, 1, %s) ON DUPLICATE KEY UPDATE "
            f"{value_col} = {value_col} + 1, {updated_col} = %s"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, [scope, now, now])
        value = CodeSequence.objects.filter(scope=scope).values_list("last_value", flat=True).get()
    return f"{CODE_PREFIX}-{jalali_year}-{value:0{CODE_PAD}d}"
