"""هم‌توانی نوشتن‌ها — «دوبار ارسال، یک‌بار اثر».

مسئلهٔ واقعی: کاربر روی دکمهٔ «رزرو» دو بار می‌زند، یا موبایل درخواست را
دوباره می‌فرستد. بدون محافظت، دو رزرو ساخته می‌شود (یا خطای گمراه‌کننده).

راه‌حل: کلید یکتای ``Idempotency-Key`` + نگه‌داری نتیجهٔ نخست و بازپخش آن.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from typing import Any

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel

DEFAULT_TTL_HOURS = 24


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


def purge_expired(*, batch: int = 1000) -> int:
    """پاک‌سازی دسته‌ای کلیدهای منقضی — کار زمان‌بندی‌شده.

    دسته‌ای حذف می‌کنیم تا در جدول بزرگ، یک تراکنش سنگین ایجاد نشود.
    """
    stale_ids = list(
        IdempotencyKey.all_objects.filter(expires_at__lt=timezone.now())
        .order_by("expires_at")
        .values_list("id", flat=True)[:batch]
    )
    if not stale_ids:
        return 0
    deleted, _ = IdempotencyKey.all_objects.filter(id__in=stale_ids).delete()
    return deleted
