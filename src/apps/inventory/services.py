"""سرویس‌های موجودی — ایجاد و برداشتن بلوک نگهداری با دلیل اجباری.

قاعدهٔ I14: هر عملیات مدیریتی **بدون دلیل** ثبت نمی‌شود. دلیل بلوک در تقویم
عمومی نمایش داده می‌شود («تعمیر»، «نظافت اساسی»)، پس هم شفافیت می‌سازد و هم
مسئولیت‌پذیری.
"""

from __future__ import annotations

from datetime import datetime
import logging

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone
from psycopg.types.range import Range

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import events, outbox
from apps.core.errors import ConflictError, ValidationError as DomainValidationError
from apps.identity.models import User
from apps.inventory.choices import BlockKind
from apps.inventory.models import REASON_MIN_LENGTH, Block, Unit
from apps.inventory.selectors import active_units

logger = logging.getLogger(__name__)


def create_block(
    *,
    start: datetime,
    end: datetime,
    kind: str,
    reason: str,
    unit: Unit | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Block:
    """ثبت بلوک نگهداری (واحد‌محور یا سراسری) با ثبت حسابرسی.

    تضمین: دو بلوک هم‌پوشان روی یک واحد از سمت دیتابیس رد می‌شود؛ خطای دیتابیس
    به پاسخ دامنه‌ای قابل‌فهم («این بازه قبلاً بلوک شده») ترجمه می‌شود.
    """
    if end <= start:
        raise DomainValidationError("پایان بلوک باید بعد از آغاز آن باشد.", code="invalid_period")
    if len((reason or "").strip()) < REASON_MIN_LENGTH:
        raise DomainValidationError(
            "ثبت دلیل برای بلوک نگهداری اجباری است (حداقل ۵ نویسه).", code="reason_required"
        )
    if kind not in BlockKind.values:
        raise DomainValidationError("نوع بلوک انتخاب‌شده معتبر نیست.", code="invalid_block_kind")

    with transaction.atomic():
        try:
            block = Block.objects.create(
                unit=unit,
                period=Range(start, end),
                kind=kind,
                reason=reason.strip(),
                created_by=actor,
            )
        except IntegrityError as exc:
            raise ConflictError(
                "برای این واحد در همین بازه بلوک دیگری ثبت شده است.", code="block_overlap"
            ) from exc

        outbox.publish(
            "block.created",
            aggregate_type="block",
            aggregate_id=block.id,
            payload={
                "block_id": str(block.id),
                "unit_code": unit.code if unit else None,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "kind": kind,
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.BLOCK_CREATED,
                actor_id=actor.id if actor else None,
                actor_label=actor.display_name if actor else "سیستم",
                target_type="block",
                target_id=str(block.id),
                reason=reason.strip(),
                metadata={
                    "unit": unit.code if unit else "سراسری",
                    "kind": kind,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                request=request,
            )
        )
    logger.info(
        "inventory_block_created",
        extra={"unit": unit.code if unit else "global", "kind": kind},
    )
    return block


def remove_block(
    *,
    block: Block,
    actor: User,
    reason: str,
    request: HttpRequest | None = None,
) -> None:
    """برداشتن بلوک (حذف نرم) با دلیل و ثبت حسابرسی."""
    if len((reason or "").strip()) < REASON_MIN_LENGTH:
        raise DomainValidationError("ثبت دلیل برداشتن بلوک اجباری است.", code="reason_required")
    with transaction.atomic():
        block.deleted_at = timezone.now()
        block.is_active = False
        block.save(update_fields=["deleted_at", "is_active", "updated_at"])
        outbox.publish(
            "block.removed",
            aggregate_type="block",
            aggregate_id=block.id,
            payload={
                "block_id": str(block.id),
                "unit_code": block.unit.code if block.unit else None,
                "reason": reason.strip(),
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.BLOCK_REMOVED,
                actor_id=actor.id,
                actor_label=actor.display_name,
                target_type="block",
                target_id=str(block.id),
                reason=reason.strip(),
                metadata={"unit": block.unit.code if block.unit else "سراسری"},
                request=request,
            )
        )
        # بلوک سراسری روی همهٔ واحدها اثر دارد؛ آزادسازی را برای همه اعلام
        # می‌کنیم تا صف انتظار فرصت را از دست ندهد.
        # نوع صریح است تا «کدام واحد، کدام بازه» در دو خط بعد هم روشن بماند.
        freed: list[tuple[Unit, datetime | None, datetime | None]] = [
            (unit, block.start, block.end)
            for unit in ([block.unit] if block.unit_id else list(active_units()))
            if unit is not None
        ]

    for freed_unit, freed_start, freed_end in freed:
        if freed_start is None or freed_end is None:  # pragma: no cover - محافظ
            continue
        events.emit(
            events.SLOT_FREED,
            {
                "unit_id": str(freed_unit.id),
                "unit_code": freed_unit.code,
                "start": freed_start.isoformat(),
                "end": freed_end.isoformat(),
                "cause": "block_removed",
                "reservation_id": None,
            },
        )
