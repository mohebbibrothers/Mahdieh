"""کدهای انسان‌خوان رزرو — «M-1405-0231» به‌جای UUID در مکالمهٔ تلفنی.

چرا کد خوانا لازم است؟ کارمند پذیرش روی تلفن می‌پرسد «کد رزروتان چیست؟».
خواندن یک UUID غیرممکن است؛ خواندن «۱۴۰۵-۰۲۳۱» ساده است. کد همچنین در پیامک
و در کارت رزرو نمایش داده می‌شود.
"""

from __future__ import annotations

from datetime import date
import logging
from typing import Final

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.errors import ConflictError
from apps.core.jalali import gregorian_to_jalali
from apps.reservation.models import CodeSequence

logger = logging.getLogger(__name__)

CODE_PREFIX: Final[str] = "M"
CODE_PAD: Final[int] = 4
MAX_ATTEMPTS: Final[int] = 5


def next_reservation_code(*, on: date | None = None) -> str:
    """تولید کد یکتای رزرو برای سال جلالی جاری.

    هم‌زمانی: ردیف شمارنده با ``select_for_update`` قفل می‌شود؛ اگر دو درخواست
    هم‌زمان برسند، یکی منتظر می‌ماند و کد بعدی را می‌گیرد (بدون شکاف و بدون
    تکرار). در نخستین رزرو سال، ساخت ردیف شمارنده ممکن است با مسابقه روبه‌رو
    شود که با تلاش مجدد حل می‌شود.
    """
    day = on or timezone.localdate()
    jalali_year, _month, _day = gregorian_to_jalali(day)
    scope = str(jalali_year)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with transaction.atomic():
                sequence, _created = CodeSequence.objects.select_for_update().get_or_create(
                    scope=scope
                )
                sequence.last_value += 1
                sequence.save(update_fields=["last_value", "updated_at"])
                return f"{CODE_PREFIX}-{jalali_year}-{sequence.last_value:0{CODE_PAD}d}"
        except IntegrityError:  # pragma: no cover - مسابقهٔ ساخت ردیف شمارنده
            logger.warning("reservation_code_sequence_race", extra={"attempt": attempt})
            continue

    raise ConflictError(  # pragma: no cover - پس از چند تلاش، شکست غیرمنتظره
        "تولید کد رزرو در این لحظه ممکن نشد؛ لطفاً دوباره تلاش کنید.",
        code="code_generation_failed",
    )
