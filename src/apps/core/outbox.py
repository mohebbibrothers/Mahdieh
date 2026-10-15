"""Outbox تراکنشی — تضمین «رزرو هرگز به سرویس بیرونی گره نمی‌خورد».

مسئله: اگر در تراکنش رزرو، ارسال پیامک شکست بخورد، یا رزرو باید برگردد
(غلط) یا رخداد گم می‌شود (بدتر). راه‌حل: رخداد را در **همان تراکنش** در
جدول Outbox بنویس؛ انتشار، مسئلهٔ بعدی است و تضمین «حداقل یک‌بار» دارد.

قاعدهٔ آهنین: ``publish()`` هرگز شبکه را لمس نمی‌کند و هرگز داخل
``transaction.on_commit`` نمی‌رود — چون باید با تراکنش اتمیک باشد.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any
from uuid import UUID

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.choices import OutboxStatus
from apps.core.models import BaseModel
from apps.core.uuid7 import uuid7

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """رخداد دامنه — داده‌ای تغییرناپذیر که گذشته را روایت می‌کند."""

    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    event_id: UUID | None = None

    def as_payload(self) -> dict[str, Any]:
        """بدنهٔ قابل‌ذخیره در Outbox."""
        return self.payload


class OutboxEvent(BaseModel):
    """صف رخدادهای دامنه — نوشته‌شده در تراکنش، منتشرشده پس از آن."""

    event_type = models.CharField(max_length=120, db_index=True, verbose_name=_("نوع رخداد"))
    aggregate_type = models.CharField(max_length=80, db_index=True, verbose_name=_("نوع تجمیع"))
    aggregate_id = models.CharField(max_length=64, db_index=True, verbose_name=_("شناسهٔ تجمیع"))
    payload = models.JSONField(default=dict, verbose_name=_("بدنه"))
    headers = models.JSONField(default=dict, blank=True, verbose_name=_("فراداده"))
    status = models.CharField(
        max_length=16,
        choices=OutboxStatus.choices,
        default=OutboxStatus.PENDING,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    occurred_at = models.DateTimeField(
        default=timezone.now, db_index=True, verbose_name=_("زمان وقوع")
    )
    published_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان انتشار"))
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name=_("تلاش‌ها"))
    last_error = models.TextField(blank=True, default="", verbose_name=_("آخرین خطا"))
    available_at = models.DateTimeField(
        default=timezone.now, db_index=True, verbose_name=_("زمان آماده‌بودن برای تلاش بعدی")
    )

    class Meta:
        verbose_name = _("رخداد Outbox")
        verbose_name_plural = _("رخدادهای Outbox")
        ordering = ("occurred_at",)
        indexes = [
            models.Index(fields=["status", "available_at"], name="outbox_status_avail_idx"),
            models.Index(fields=["aggregate_type", "aggregate_id"], name="outbox_aggregate_idx"),
        ]

    def __str__(self) -> str:
        """نمای خوانا برای پنل مدیریت."""
        return f"{self.event_type} · {self.aggregate_id} · {self.status}"


def publish(
    event_type: str,
    *,
    aggregate_type: str,
    aggregate_id: str | UUID,
    payload: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> OutboxEvent:
    """ثبت رخداد دامنه در تراکنش جاری.

    باید **داخل** ``transaction.atomic()`` فراخوانی شود؛ در غیر این صورت
    رخداد می‌تواند از وضعیت داده جدا شود.
    """
    if not transaction.get_connection().in_atomic_block:
        logger.warning(
            "outbox_publish_outside_transaction",
            extra={"event_type": event_type, "aggregate_type": aggregate_type},
        )
    return OutboxEvent.all_objects.create(
        id=uuid7(),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=payload or {},
        headers=headers or {},
        occurred_at=timezone.now(),
        available_at=timezone.now(),
    )


def fetch_pending_batch(*, limit: int = 100) -> list[OutboxEvent]:
    """برداشت یک دستهٔ رخداد آمادهٔ انتشار با قفل بدون انتظار.

    ``SKIP LOCKED`` اجازه می‌دهد چند رله هم‌زمان کار کنند بدون ددلاک و بدون
    دوباره‌فرستادن یک رکورد.

    نکتهٔ مهم: قفل ردیفی فقط داخل تراکنش معنا دارد؛ این تابع خودش تضمین
    می‌کند که در تراکنش اجرا شود تا فراخوانی سهل‌انگارانه باعث خطای مبهم نشود.
    """
    with transaction.atomic():
        return list(
            OutboxEvent.all_objects.select_for_update(skip_locked=True)
            .filter(status=OutboxStatus.PENDING, available_at__lte=timezone.now())
            .order_by("occurred_at")[:limit]
        )


def mark_published(event: OutboxEvent) -> None:
    """ثبت انتشار موفق."""
    event.status = OutboxStatus.PUBLISHED
    event.published_at = timezone.now()
    event.attempts += 1
    event.save(update_fields=["status", "published_at", "attempts", "updated_at"])


def mark_failed(event: OutboxEvent, error: str, *, backoff_seconds: int) -> None:
    """ثبت شکست با عقب‌نشینی نمایی؛ پس از سقف تلاش ⇒ صف مرده."""
    event.attempts += 1
    event.last_error = error[:2000]
    if event.attempts >= MAX_ATTEMPTS:
        event.status = OutboxStatus.DEAD
        logger.error(
            "outbox_event_dead",
            extra={"event_type": event.event_type, "event_id": str(event.id)},
        )
    else:
        event.status = OutboxStatus.PENDING
        event.available_at = timezone.now() + timedelta(seconds=backoff_seconds)
    event.save(update_fields=["status", "attempts", "last_error", "available_at", "updated_at"])
