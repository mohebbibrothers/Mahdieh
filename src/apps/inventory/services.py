"""سرویس‌های موجودی — ایجاد و برداشتن بلوک نگهداری با دلیل اجباری.

قاعدهٔ I14: هر عملیات مدیریتی **بدون دلیل** ثبت نمی‌شود. دلیل بلوک در تقویم
عمومی نمایش داده می‌شود («تعمیر»، «نظافت اساسی»)، پس هم شفافیت می‌سازد و هم
مسئولیت‌پذیری.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import cast

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import events, outbox
from apps.core.errors import ConflictError, ValidationError as DomainValidationError
from apps.core.jalali import format_jalali
from apps.core.models import SoftDeleteQuerySet
from apps.identity.models import User
from apps.inventory import official_calendar
from apps.inventory.choices import BlockKind, OccasionSource
from apps.inventory.models import REASON_MIN_LENGTH, Block, LunarMonthStart, Occasion, Unit
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
                starts_at=start,
                ends_at=end,
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


# ── همگام‌سازی تقویم رسمی ─────────────────────────────────────────────────────────────────────

#: پنجرهٔ پیش‌فرض همگام‌سازی: یک ماه گذشته (برای سابقهٔ رزروها) تا ۱۳ ماه آینده (افق تقویم و اعلان‌ها).
SYNC_DAYS_BACK = 31
SYNC_DAYS_AHEAD = 400


@dataclass(frozen=True, slots=True)
class OccasionSyncReport:
    """خلاصهٔ یک اجرای همگام‌سازی — برای خروجی دستور مدیریتی و لاگ."""

    start: date
    end: date
    created: int
    updated: int
    removed: int
    provisional: int
    total: int


def default_sync_window(today: date | None = None) -> tuple[date, date]:
    """پنجرهٔ پیش‌فرض همگام‌سازی نسبت به امروزِ منطقهٔ زمانی سایت."""
    base = today or timezone.localdate()
    return base - timedelta(days=SYNC_DAYS_BACK), base + timedelta(days=SYNC_DAYS_AHEAD)


@transaction.atomic
def sync_official_occasions(*, start: date, end: date) -> OccasionSyncReport:
    """همگام‌سازی idempotent مناسبت‌های تقویم رسمی در بازهٔ [start, end].

    • ردیف تازه ⇒ ساخته می‌شود (سطح تقاضا و توضیح پیش‌فرض از فهرست رسمی).
    • ردیف موجود ⇒ فقط فیلدهای «تقویمی» (عنوان، تاریخ، تعطیلی، تاریخ قمری، تقریبی‌بودن) به‌روز
      می‌شوند؛ آنچه مدیر تنظیم کرده (سطح تقاضا، توضیح، فعال‌بودن) دست‌نخورده می‌ماند.
    • ردیفی که مدیر حذف (نرم) کرده هرگز دوباره زنده نمی‌شود.
    • ردیف رسمیِ درون بازه که دیگر تولید نمی‌شود (جابه‌جایی ماه قمری) ⇒ برداشته می‌شود.
    """
    # جدول رسمی داخلی پشتوانه است؛ ردیف‌های پایگاه‌داده (اصلاح مدیر با اعلام رؤیت) بر آن مقدم‌اند.
    starts = {(y, m): d for y, m, d in official_calendar.OFFICIAL_MONTH_STARTS_1405}
    starts.update(
        {
            (row.hijri_year, row.hijri_month): row.starts_on
            for row in LunarMonthStart.objects.filter(is_active=True)
        }
    )
    generated = {g.key: g for g in official_calendar.generate(start, end, starts)}
    existing = {
        o.source_key: o
        for o in Occasion.all_objects.filter(source_key__in=list(generated)).select_for_update()
    }
    created = updated = 0
    for key, g in generated.items():
        fields = {
            "title": g.event.title,
            "kind": g.event.kind,
            "date_gregorian": g.day,
            "date_jalali": format_jalali(g.day, numeric=True),
            "is_holiday": g.event.holiday,
            "hijri_date": g.hijri_label,
            "is_provisional": g.provisional,
            "is_fixed_every_year": g.event.calendar == "j",
        }
        row = existing.get(key)
        if row is None:
            Occasion.objects.create(
                source=OccasionSource.OFFICIAL,
                source_key=key,
                demand_level=g.event.demand,
                description=official_calendar.DESCRIPTIONS[g.event.demand],
                **fields,
            )
            created += 1
            continue
        changed = [name for name, value in fields.items() if getattr(row, name) != value]
        if changed:
            for name in changed:
                setattr(row, name, fields[name])
            row.save(update_fields=[*changed, "updated_at"])
            updated += 1
    stale = Occasion.all_objects.filter(
        source=OccasionSource.OFFICIAL, date_gregorian__gte=start, date_gregorian__lte=end
    ).exclude(source_key__in=list(generated))
    # ردیف تولیدی بی‌پشتوانه حذف فیزیکی می‌شود (کلید یکتا آزاد بماند)؛ حذف نرم مدیر جداست.
    removed, _detail = cast("SoftDeleteQuerySet[Occasion]", stale).hard_delete()
    report = OccasionSyncReport(
        start=start,
        end=end,
        created=created,
        updated=updated,
        removed=removed,
        provisional=sum(1 for g in generated.values() if g.provisional),
        total=len(generated),
    )
    logger.info(
        "occasions.synced",
        extra={
            "occasions_created": created,
            "occasions_updated": updated,
            "occasions_removed": removed,
            "occasions_total": len(generated),
        },
    )
    return report
