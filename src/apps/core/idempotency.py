"""هم‌توانی نوشتن‌ها — «دوبار ارسال، یک‌بار اثر».

مسئلهٔ واقعی: کاربر روی دکمهٔ «رزرو» دو بار می‌زند، یا موبایل درخواست را
دوباره می‌فرستد. بدون محافظت، دو رزرو ساخته می‌شود (یا خطای گمراه‌کننده).

راه‌حل: کلید یکتای ``Idempotency-Key`` + نگه‌داری نتیجهٔ نخست و بازپخش آن.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from typing import Any, cast

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.errors import ConflictError, IdempotencyConflictError
from apps.core.models import BaseModel, SoftDeleteQuerySet

DEFAULT_TTL_HOURS = 24

IDEMPOTENCY_HEADER = "Idempotency-Key"
"""نام استاندارد هدر هم‌توانی — یک قرارداد مشترک بین ماژول‌ها، یک نقطهٔ حقیقت."""


def fingerprint(payload: Any, *, extra: str = "") -> str:
    """اثر انگشت پایدار بدنهٔ درخواست — برای کشف استفادهٔ نادرست از کلید."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(f"{extra}|{canonical}".encode())
    return digest.hexdigest()


class IdempotencyKey(BaseModel):
    """رکورد کلید هم‌توانی با نتیجهٔ ذخیره‌شده."""

    key = models.CharField(max_length=128, verbose_name=_("کلید"))
    scope = models.CharField(max_length=120, verbose_name=_("دامنه (نقطهٔ پایانی)"))
    actor_ref = models.CharField(max_length=64, blank=True, default="", verbose_name=_("کنش‌گر"))
    request_fingerprint = models.CharField(
        max_length=64, blank=True, default="", verbose_name=_("اثر انگشت درخواست")
    )
    response_status = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name=_("کد پاسخ")
    )
    response_body = models.JSONField(null=True, blank=True, verbose_name=_("بدنهٔ پاسخ"))
    locked_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان قفل"))
    expires_at = models.DateTimeField(db_index=True, verbose_name=_("انقضا"))

    class Meta:
        verbose_name = _("کلید هم‌توانی")
        verbose_name_plural = _("کلیدهای هم‌توانی")
        constraints = [
            models.UniqueConstraint(fields=["key", "scope"], name="uniq_idempotency_key_scope"),
        ]
        indexes = [models.Index(fields=["expires_at"], name="idem_expires_idx")]

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"{self.scope}:{self.key}"


class IdempotencyOutcome(models.TextChoices):
    """نتیجهٔ تلاش برای رزرو یک کلید هم‌توانی."""

    RESERVED = "reserved", _("کلید رزرو شد — ادامه بده")
    REPLAY = "replay", _("پاسخ قبلی بازپخش شود")
    CONFLICT = "conflict", _("کلید با درخواست دیگری استفاده شده")
    IN_PROGRESS = "in_progress", _("درخواست دیگری در حال پردازش است")


def reserve_key(
    *, key: str, scope: str, actor_ref: str = "", payload: Any = None
) -> tuple[IdempotencyOutcome, IdempotencyKey | None]:
    """تلاش برای رزرو کلید پیش از اجرای عملیات.

    بازگشت یکی از حالت‌های ``IdempotencyOutcome``:
        • ``RESERVED`` ⇒ اجرا کن، سپس ``store_result`` را صدا بزن.
        • ``REPLAY`` ⇒ همان پاسخ قبلی را برگردان (کاربر دوباره فرستاده).
        • ``CONFLICT`` ⇒ همان کلید با بدنهٔ متفاوت ⇒ خطای ۴۰۹.
        • ``IN_PROGRESS`` ⇒ درخواست هم‌زمانِ دیگر در جریان است.
    """
    fp = fingerprint(payload, extra=scope) if payload is not None else ""
    now = timezone.now()
    expires = now + timedelta(hours=DEFAULT_TTL_HOURS)

    try:
        with transaction.atomic():
            record = IdempotencyKey.all_objects.create(
                key=key,
                scope=scope,
                actor_ref=actor_ref,
                request_fingerprint=fp,
                locked_at=now,
                expires_at=expires,
            )
        return IdempotencyOutcome.RESERVED, record
    except IntegrityError:
        existing = IdempotencyKey.all_objects.filter(key=key, scope=scope).first()
        if existing is None:  # pragma: no cover - مسابقهٔ نادر
            return IdempotencyOutcome.IN_PROGRESS, None
        if existing.request_fingerprint and fp and existing.request_fingerprint != fp:
            return IdempotencyOutcome.CONFLICT, existing
        if existing.response_status is not None:
            return IdempotencyOutcome.REPLAY, existing
        return IdempotencyOutcome.IN_PROGRESS, existing


def store_result(record: IdempotencyKey, *, status: int, body: Any) -> None:
    """ذخیرهٔ نتیجهٔ عملیات برای بازپخش در درخواست‌های تکراری."""
    record.response_status = status
    record.response_body = body
    record.save(update_fields=["response_status", "response_body", "updated_at"])


@dataclass(frozen=True, slots=True)
class IdempotentOutcome:
    """نتیجهٔ اجرای یک عملیات هم‌توان — برای پاسخ HTTP."""

    status: int
    body: Any
    replayed: bool


def execute_idempotent(
    *,
    key: str,
    scope: str,
    actor_ref: str,
    payload: Any,
    action: Callable[[], tuple[int, Any]],
) -> IdempotentOutcome:
    """اجرای عملیات با تضمین «دوبار ارسال، یک‌بار اثر».

    ترتیب کار:
        ۱. اگر کلید تازه است ⇒ رزرو کلید، اجرای عملیات، ذخیرهٔ نتیجه.
        ۲. اگر همان کلید و همان بدنه دوباره آمد ⇒ **بازپخش همان پاسخ** (نه اثر دوباره).
        ۳. اگر همان کلید با بدنهٔ متفاوت ⇒ خطای ۴۰۹ (اشتباه برنامه‌نویسی کلاینت).
        ۴. اگر درخواست هم‌زمان دیگری در جریان باشد ⇒ ۴۰۹ با پیام روشن.

    ``action`` باید ``(status_code, body)`` برگرداند؛ بدنه باید JSON-سریال‌پذیر باشد.
    """
    outcome, record = reserve_key(key=key, scope=scope, actor_ref=actor_ref, payload=payload)
    if outcome is IdempotencyOutcome.REPLAY and record is not None:
        return IdempotentOutcome(
            status=int(record.response_status or 200), body=record.response_body, replayed=True
        )
    if outcome is IdempotencyOutcome.CONFLICT:
        raise IdempotencyConflictError("این کلید هم‌توانی پیش‌تر با درخواست دیگری استفاده شده است.")
    if outcome is IdempotencyOutcome.IN_PROGRESS:
        raise ConflictError(
            "درخواست هم‌زمان دیگری در حال پردازش است؛ یک لحظه بعد دوباره تلاش کنید.",
            code="idempotent_in_progress",
        )

    status_code, body = action()
    if record is not None:
        store_result(record, status=status_code, body=body)
    return IdempotentOutcome(status=status_code, body=body, replayed=False)


def purge_expired(*, batch: int = 1000) -> int:
    """پاک‌سازی دسته‌ای کلیدهای منقضی — کار زمان‌بندی‌شده.

    دسته‌ای حذف می‌کنیم تا در جدول بزرگ، یک تراکنش سنگین ایجاد نشود.

    دو نکتهٔ گران‌قیمت که آزمون نگهداری لو داد:
        1. حذف **فیزیکی** لازم است (``hard_delete``): مدل ما حذف نرم دارد و
           ``delete()`` فقط ``deleted_at`` می‌گذارد — نگهداری بدنهٔ پاسخ‌ها روی
           دیسک، همان چیزی است که می‌خواستیم پاک شود.
        2. ``hard_delete`` بازگشتِ ``(count, detail)`` دارد، نه عدد خالی؛ فرض
           غلط روی خروجی، نخستین اجرای واقعی را با ``TypeError`` می‌شکست.
    """
    stale_ids = list(
        IdempotencyKey.all_objects.filter(expires_at__lt=timezone.now())
        .order_by("expires_at")
        .values_list("id", flat=True)[:batch]
    )
    if not stale_ids:
        return 0
    # ``hard_delete`` روی QuerySet حذف‌نرم تعریف شده؛ مدیرِ همهٔ‌رکوردها نوعش را
    # برای بازبین نوع‌ها روشن نمی‌کند، پس صریح می‌گوییم کدام QuerySet است.
    stale = cast(
        "SoftDeleteQuerySet[IdempotencyKey]",
        IdempotencyKey.all_objects.filter(id__in=stale_ids),
    )
    deleted, _ = stale.hard_delete()
    return int(deleted)
