"""سرویس‌های صف انتظار — «سانس آزاد شد، منصفانه به نفر بعدی برسد».

سناریوی کانونی (S3 نقشهٔ راه):
    ۱) زائر الف رزرو دارد؛ زائر ب درخواست انتظار برای همان بازه ثبت می‌کند.
    ۲) زائر الف لغو می‌کند ⇒ رخداد ``slot.freed`` منتشر می‌شود.
    ۳) به نفر اول صف **پیشنهاد انحصاری** با مهلت می‌رود (پیامک + لینک یک‌ضربه‌ای).
    ۴) زائر ب می‌پذیرد ⇒ رزرو ساخته می‌شود و درخواست ``converted`` می‌شود.
    ۵) اگر نپذیرد/مهلت بگذرد ⇒ نفر بعدی؛ پس از ``max_offers_before_requeue``
       پیشنهاد، درخواست به **انتهای صف** برمی‌گردد تا صف هرگز قفل نشود.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
import structlog

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import events, outbox
from apps.core.errors import (
    ConflictError,
    PolicyViolationError,
    ValidationError as DomainValidationError,
)
from apps.core.jalali import clock_label
from apps.core.text import to_persian_digits as fa
from apps.inventory.lattice import count_cells, snap_forward
from apps.notifications.choices import SmsTemplateKey
from apps.notifications.services import send_sms
from apps.reservation import services as reservation_services
from apps.reservation.choices import ReservationSource
from apps.reservation.policy import lattice_config, policy_bool, policy_int, quiet_hours
from apps.waitlist.choices import DeclineReason, OfferStatus, WaitlistStatus
from apps.waitlist.models import SlotOffer, WaitlistEntry
from apps.waitlist.selectors import queue_key_expression
from apps.waitlist.tokens import ACCEPT, DECLINE, offer_urls

logger = structlog.get_logger(__name__)

REASON_MIN_LENGTH = 5
QUEUE_ORDER = ("queue_key", "id")


# ─── ابزارهای داخلی ─────────────────────────────────────────────────────────
def _violation(code: str, message: str) -> dict[str, str]:
    """ساخت یک تخلف ساختاریافته برای پاسخ ۴۲۲."""
    return {"code": code, "message": message}


def _offer_deadline(now: datetime) -> datetime:
    """مهلت پاسخ به پیشنهاد — با احترام به شب‌های سکوت.

    منطق: مهلت پیش‌فرض ``waitlist.offer_window_minutes`` است؛ اما اگر پایان مهلت
    در بازهٔ سکوت پیامک بیفتد (نیمه‌شب)، مهلت تا پایان بازهٔ سکوت + نصف پنجره
    تمدید می‌شود. زائر بیدار نشده نباید فرصت را از دست بدهد.
    """
    window = timedelta(minutes=policy_int("waitlist.offer_window_minutes"))
    deadline = now + window
    if quiet_hours(deadline):
        end_hour = policy_int("notification.quiet_hours_end")
        local = timezone.localtime(deadline)
        wake = local.replace(hour=end_hour % 24, minute=0, second=0, microsecond=0)
        if wake <= local:
            wake += timedelta(days=1)
        deadline = wake + window / 2
    return deadline


def _slot_key(*, unit_id: Any, start: datetime, end: datetime) -> str:
    """شناسهٔ یکتای سانس (واحد + بازه) برای هم‌راستایی پیشنهادها."""
    return SlotOffer.key_for(unit_id=unit_id, start=start, end=end)


def active_entry_count(guest: Any, *, exclude_pk: Any | None = None) -> int:
    """شمار درخواست‌های «در جریان» یک زائر (برای سهمیه)."""
    queryset = WaitlistEntry.objects.filter(
        guest=guest, status__in=WaitlistStatus.active_statuses()
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return int(queryset.count())


def queue_position(entry: WaitlistEntry) -> int:
    """نوبت فعلی یک درخواست در صف (۱ = نفر اول).

    نوبت با همان معیار عدالتِ پیشنهاد محاسبه می‌شود: نخستین زمانِ «ورود یا
    بازگشت به صف»، پس درخواست بازگشته به انتهای صف می‌رود.
    """
    if not entry.is_active_entry:
        return 0
    # درخواست «هر واحد» با همهٔ داوطلبان همان بازه سنجیده می‌شود؛ درخواست
    # واحد‌محور، هم با صف همان واحد و هم با صف‌های بی‌واحد (چون آن‌ها هم می‌توانند
    # همان سانس را بگیرند).
    scope = Q(unit__isnull=True) | Q(unit_id=entry.unit_id) if entry.unit_id else Q()
    candidates = (
        WaitlistEntry.objects.filter(status=WaitlistStatus.WAITING)
        .filter(scope)
        .filter(starts_at__lte=entry.starts_at, ends_at__gte=entry.ends_at)
        .annotate(queue_key=queue_key_expression())
        .order_by(*QUEUE_ORDER)
        .values_list("id", flat=True)
    )
    for index, entry_id in enumerate(candidates, start=1):
        if entry_id == entry.id:
            return index
    # درخواست در وضعیت «پیشنهاد داده‌شده» است یا در این پنجره رقیبی ندارد.
    ahead = (
        WaitlistEntry.objects.filter(status=WaitlistStatus.WAITING)
        .filter(scope)
        .filter(starts_at__lte=entry.starts_at, ends_at__gte=entry.ends_at)
        .count()
    )
    return int(ahead) + 1


def matching_entries(
    *, unit_id: Any, start: datetime, end: datetime, exclude_ids: Sequence[Any] = ()
) -> list[WaitlistEntry]:
    """درخواست‌های واجد شرطِ یک سانس، به ترتیب عدالت صف."""
    queryset = (
        WaitlistEntry.objects.filter(status=WaitlistStatus.WAITING)
        .filter(Q(unit__isnull=True) | Q(unit_id=unit_id))
        .filter(starts_at__gte=start, ends_at__lte=end)
        .annotate(queue_key=queue_key_expression())
        .order_by(*QUEUE_ORDER)
    )
    if exclude_ids:
        queryset = queryset.exclude(id__in=list(exclude_ids))
    return list(queryset)


# ─── ثبت و انصراف ───────────────────────────────────────────────────────────
def join_waitlist(
    *,
    guest: Any,
    unit: Any | None,
    start: datetime,
    end: datetime,
    guests_count: int,
    note: str = "",
    source: str = "web",
    request: Any | None = None,
) -> WaitlistEntry:
    """ثبت درخواست انتظار با تمام قیدهای سیاست.

    اگر سانس **همین حالا آزاد است**، درخواست انتظار معنا ندارد؛ خطای راهنما
    برگردانده می‌شود تا زائر مستقیم رزرو کند (تجربهٔ کاربری بهتر، نه صف بی‌دلیل).
    """
    if end <= start:
        raise DomainValidationError("پایان بازه باید بعد از آغاز آن باشد.", code="bad_window")
    if guests_count < 1:
        raise DomainValidationError("تعداد نفرات باید دست‌کم یک باشد.", code="bad_guests")

    config = lattice_config()
    try:
        cells = count_cells(start, end, config)
    except ValueError as exc:
        # قاعدهٔ شبکه (نیم‌روزهای ۰۸:۰۰ و ۲۰:۰۰) یک قاعدهٔ دامنه است، نه خطای
        # برنامه‌نویس؛ پس به خطای قابل‌نمایش تبدیل می‌شود تا هم وب و هم API
        # پیام روشن بدهند، نه ۵۰۰.
        raise DomainValidationError(
            "بازهٔ درخواست باید روی نیم‌روزهای اقامت باشد (آغاز ۰۸:۰۰ یا ۲۰:۰۰).",
            code="off_grid",
        ) from exc
    max_cells = policy_int("waitlist.max_cells_per_entry")
    if cells > max_cells:
        raise PolicyViolationError(
            f"درخواست انتظار حداکثر {max_cells} نیم‌روز می‌تواند باشد.",
            extensions={
                "violations": [_violation("too_long", "بازهٔ درخواست بیش از حد طولانی است.")]
            },
        )

    now = timezone.now()
    horizon_days = policy_int("reservation.booking_horizon_days")
    if start < now + timedelta(hours=policy_int("reservation.min_lead_time_hours")):
        raise PolicyViolationError(
            "این بازه از پیش‌تر از مهلت مجاز آغاز می‌شود؛ بازهٔ دیگری انتخاب کنید.",
            extensions={"violations": [_violation("lead_time", "زمان کافی تا آغاز اقامت نیست.")]},
        )
    if (start - now).days > horizon_days:
        raise PolicyViolationError(
            f"درخواست انتظار تا {horizon_days} روز آینده پذیرفته می‌شود.",
            extensions={"violations": [_violation("horizon", "بازهٔ درخواست دور از افق رزرو است.")]},
        )

    quota = policy_int("waitlist.max_active_per_guest")
    if active_entry_count(guest) >= quota:
        raise PolicyViolationError(
            "هم‌اکنون یک درخواست در جریان دارید؛ تا بسته شدن آن صبر کنید.",
            extensions={
                "violations": [_violation("waitlist_quota", "سهمیهٔ درخواست هم‌زمان پر است.")]
            },
        )

    if unit is not None and _slot_is_bookable(unit_id=unit.id, start=start, end=end):
        raise ConflictError(
            "این سانس همین حالا آزاد است — همین‌جا رزرو کنید، نیازی به صف نیست.",
            code="slot_already_free",
        )

    if unit is None:  # جست‌وجوی نزدیک‌ترین واحد آزاد در همان بازه
        free_unit = _free_unit_for(start=start, end=end)
        if free_unit is not None:
            raise ConflictError(
                f"واحد {free_unit.code} در همین بازه آزاد است — همین‌جا رزرو کنید.",
                code="slot_already_free",
            )

    with transaction.atomic():
        entry = WaitlistEntry(
            guest=guest,
            unit=unit,
            starts_at=snap_forward(start, config),
            ends_at=end,
            cells_count=cells,
            guests_count=guests_count,
            guest_note=note[:200],
            source=source[:16],
            position_hint=(WaitlistEntry.objects.filter(status=WaitlistStatus.WAITING).count() + 1),
        )
        try:
            entry.save()
        except IntegrityError as exc:
            raise PolicyViolationError(
                "درخواست فعال دیگری برای شما در صف است.",
                extensions={
                    "violations": [_violation("duplicate_entry", "یک درخواست فعال دارید.")]
                },
            ) from exc
        outbox.publish(
            "waitlist.joined",
            aggregate_type="waitlist_entry",
            aggregate_id=entry.id,
            payload={
                "entry_id": str(entry.id),
                "guest_id": str(guest.id),
                "unit": unit.code if unit else None,
                "start": entry.starts_at.isoformat(),
                "end": entry.ends_at.isoformat(),
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.WAITLIST_JOINED,
                actor_id=guest.id,
                actor_label=guest.display_name,
                target_type="waitlist_entry",
                target_id=str(entry.id),
                metadata={
                    "cells": cells,
                    "unit": unit.code if unit is not None else "*",
                },
                request=request,
            )
        )

    logger.info(
        "waitlist_entry_created",
        entry_id=str(entry.id),
        position=entry.position_hint,
        cells=cells,
    )
    send_sms(
        phone=guest.phone,
        template_key=SmsTemplateKey.WAITLIST_JOINED,
        context={
            "unit": unit.title if unit else "هر واحد آزاد",
            "window": entry.window_label,
            "position": fa(queue_position(entry)),
            "full_name": guest.display_name,
        },
        dedup_key=f"waitlist-join:{entry.id}",
    )
    return entry


def withdraw_entry(
    *,
    entry: WaitlistEntry,
    by: Any,
    reason: str = "",
    request: Any | None = None,
) -> WaitlistEntry:
    """انصراف از صف — توسط خود زائر یا کارکنان."""
    is_staff = bool(getattr(by, "is_staff_member", False))
    if not is_staff and entry.guest_id != by.id:
        raise ConflictError("این درخواست به شما تعلق ندارد.", code="not_owner")
    if is_staff and len(reason.strip()) < REASON_MIN_LENGTH:
        raise PolicyViolationError("ثبت دلیل برای انصراف از سوی کارکنان اجباری است.")
    if not entry.is_active_entry:
        raise ConflictError(f"این درخواست در وضعیت «{entry.status_label}» است.", code="not_active")

    with transaction.atomic():
        locked = WaitlistEntry.objects.select_for_update().get(pk=entry.pk)
        locked.mark(WaitlistStatus.WITHDRAWN)
        SlotOffer.objects.filter(entry=locked, status=OfferStatus.OPEN).update(
            status=OfferStatus.SUPERSEDED, responded_at=timezone.now()
        )
        audit_record(
            AuditEntry(
                action=AuditAction.WAITLIST_WITHDRAWN,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="waitlist_entry",
                target_id=str(locked.id),
                reason=reason or "انصراف زائر",
                metadata={"by_staff": is_staff},
                request=request,
            )
        )
    events.emit(events.OFFER_EXPIRED, {"reason": "withdrawn", "entry_id": str(entry.id)})
    return locked


# ─── پیشنهاد و آبشار ────────────────────────────────────────────────────────
def offer_freed_slot(
    *,
    unit_id: Any,
    start: datetime,
    end: datetime,
    reservation_id: Any | None = None,
    exclude_entry_ids: Sequence[Any] = (),
    now: datetime | None = None,
) -> SlotOffer | None:
    """پیشنهاد یک سانس آزادشده به نفر اول صف (اگر مسیر آزادسازی مجاز باشد)."""
    moment = now or timezone.now()
    if not policy_bool("waitlist.auto_offer_enabled"):
        logger.info("waitlist_auto_offer_disabled", unit=str(unit_id))
        return None
    slot_key = _slot_key(unit_id=unit_id, start=start, end=end)
    if SlotOffer.objects.filter(slot_key=slot_key, status=OfferStatus.OPEN).exists():
        return None  # همین حالا پیشنهاد بازی برای این سانس هست

    entries = matching_entries(unit_id=unit_id, start=start, end=end, exclude_ids=exclude_entry_ids)
    if not entries:
        return None
    return _create_offer(
        entry=entries[0],
        unit_id=unit_id,
        start=start,
        end=end,
        reservation_id=reservation_id,
        now=moment,
    )


def _create_offer(
    *,
    entry: WaitlistEntry,
    unit_id: Any,
    start: datetime,
    end: datetime,
    reservation_id: Any | None,
    now: datetime,
) -> SlotOffer | None:
    """ساخت پیشنهاد (در تراکنش) + پیامک + حسابرسی."""
    from apps.inventory.models import Unit

    unit = Unit.objects.get(pk=unit_id)
    deadline = _offer_deadline(now)
    with transaction.atomic():
        locked = WaitlistEntry.objects.select_for_update().get(pk=entry.pk)
        if locked.status != WaitlistStatus.WAITING:  # در همین لحظه تغییر کرده
            return None
        try:
            offer = SlotOffer.objects.create(
                entry=locked,
                reservation_id=reservation_id,
                unit=unit,
                starts_at=start,
                ends_at=end,
                slot_key=_slot_key(unit_id=unit_id, start=start, end=end),
                expires_at=deadline,
            )
        except IntegrityError:
            # مسابقه: پیشنهاد رقیب پیشی گرفت؛ نفر بعدی در نوبت بعدی می‌گیرد.
            logger.info("offer_race_lost", entry_id=str(locked.id))
            return None
        locked.status = WaitlistStatus.OFFERED
        locked.offers_sent += 1
        locked.last_offered_at = now
        locked.save(update_fields=["status", "offers_sent", "last_offered_at", "updated_at"])
        outbox.publish(
            "waitlist.offered",
            aggregate_type="waitlist_entry",
            aggregate_id=locked.id,
            payload={
                "offer_id": str(offer.id),
                "entry_id": str(locked.id),
                "unit": unit.code,
                "expires_at": deadline.isoformat(),
                "attempt": locked.offers_sent,
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.WAITLIST_OFFERED,
                actor_label="سامانه",
                target_type="waitlist_entry",
                target_id=str(locked.id),
                metadata={
                    "unit": unit.code,
                    "attempt": locked.offers_sent,
                    "expires_at": deadline.isoformat(),
                },
            )
        )

    urls = offer_urls(offer.id)
    send_sms(
        phone=locked.guest.phone,
        template_key=SmsTemplateKey.OFFER_CREATED,
        context={
            "unit": unit.title,
            "expires_at": clock_label(deadline),
            "window": locked.window_label,
            "full_name": locked.guest.display_name,
            # لینک‌های امضاشده: زائر بدون ورود دوباره، با یک ضربه سانس را می‌گیرد.
            "accept_url": urls[ACCEPT],
            "decline_url": urls[DECLINE],
            # الگوی رسمی «{link}» را می‌خواهد؛ همان لینک پذیرش، برای یک‌ضربه‌ای‌بودن.
            "url": urls[ACCEPT],
        },
        dedup_key=f"offer:{offer.id}",
    )
    logger.info(
        "slot_offered",
        entry_id=str(locked.id),
        unit=unit.code,
        attempt=locked.offers_sent,
    )
    return offer


def accept_offer(*, offer: SlotOffer, by: Any, request: Any | None = None) -> tuple[SlotOffer, Any]:
    """پذیرش یک‌ضربه‌ای پیشنهاد ⇒ ساخت رزرو واقعی.

    اگر سانس در این فاصله توسط کسی دیگر پر شده باشد، پیشنهاد باطل و به نفر
    بعدی پیشنهاد داده می‌شود؛ زائر پیام توضیح می‌گیرد (نه خطای خشک).
    """
    entry = offer.entry
    if not bool(getattr(by, "is_staff_member", False)) and entry.guest_id != by.id:
        raise ConflictError("این پیشنهاد به شما تعلق ندارد.", code="not_owner")
    if offer.status != OfferStatus.OPEN:
        raise ConflictError(
            f"این پیشنهاد در وضعیت «{offer.get_status_display()}» است.", code="offer_closed"
        )
    if offer.expires_at <= timezone.now():
        raise ConflictError("مهلت پاسخ به این پیشنهاد گذشته است.", code="offer_expired")

    try:
        reservation = reservation_services.create_reservation(
            guest=entry.guest,
            unit=offer.unit,
            start=offer.starts_at,
            end=offer.ends_at,
            guests_count=entry.guests_count,
            source=ReservationSource.WAITLIST,
            actor=by,
            guest_note=entry.guest_note,
            staff_note=f"از صف انتظار (پیشنهاد {offer.id})"[:300],
            request=request,
        )
    except (ConflictError, PolicyViolationError) as exc:
        _invalidate_offer(offer, reason="slot_lost")
        next_offer = offer_freed_slot(
            unit_id=offer.unit_id,
            start=offer.starts_at,
            end=offer.ends_at,
            exclude_entry_ids=[entry.id],
        )
        logger.warning("offer_accept_failed", offer_id=str(offer.id), error=str(exc))
        raise ConflictError(
            "این سانس همین حالا پر شد؛ پیشنهاد بعدی برای شما در راه است."
            if next_offer is None
            else "این سانس همین حالا پر شد؛ به نفر بعدی صف پیشنهاد داده شد.",
            code="slot_taken",
        ) from exc

    with transaction.atomic():
        offer.status = OfferStatus.ACCEPTED
        offer.responded_at = timezone.now()
        offer.created_reservation = reservation
        offer.save(update_fields=["status", "responded_at", "created_reservation", "updated_at"])
        entry.converted_reservation = reservation
        entry.mark(WaitlistStatus.CONVERTED, extra_fields=["converted_reservation"])
        audit_record(
            AuditEntry(
                action=AuditAction.WAITLIST_ACCEPTED,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="waitlist_entry",
                target_id=str(entry.id),
                metadata={"offer_id": str(offer.id), "reservation": reservation.code},
                request=request,
            )
        )

    send_sms(
        phone=entry.guest.phone,
        template_key=SmsTemplateKey.OFFER_ACCEPTED,
        context={
            "code": reservation.code,
            "unit": offer.unit.title,
            "full_name": entry.guest.display_name,
        },
        dedup_key=f"offer-accepted:{offer.id}",
    )
    events.emit(
        events.OFFER_ACCEPTED,
        {"offer_id": str(offer.id), "entry_id": str(entry.id), "code": reservation.code},
    )
    return offer, reservation


def decline_offer(
    *,
    offer: SlotOffer,
    by: Any,
    reason_kind: str = DeclineReason.TIME_NOT_SUITABLE,
    request: Any | None = None,
) -> SlotOffer:
    """رد پیشنهاد ⇒ سانس بلافاصله به نفر بعدی صف می‌رود."""
    entry = offer.entry
    if not bool(getattr(by, "is_staff_member", False)) and entry.guest_id != by.id:
        raise ConflictError("این پیشنهاد به شما تعلق ندارد.", code="not_owner")
    if offer.status != OfferStatus.OPEN:
        raise ConflictError("این پیشنهاد دیگر باز نیست.", code="offer_closed")

    with transaction.atomic():
        offer.status = OfferStatus.DECLINED
        offer.responded_at = timezone.now()
        offer.decline_reason = reason_kind[:32]
        offer.save(update_fields=["status", "responded_at", "decline_reason", "updated_at"])
        entry.status = WaitlistStatus.DECLINED
        _requeue_or_close(entry, now=timezone.now())
        audit_record(
            AuditEntry(
                action=AuditAction.WAITLIST_DECLINED,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="waitlist_entry",
                target_id=str(entry.id),
                reason=str(DeclineReason(reason_kind).label) if reason_kind else "",
                metadata={"offer_id": str(offer.id), "attempt": entry.offers_sent},
                request=request,
            )
        )

    next_offer = offer_freed_slot(
        unit_id=offer.unit_id,
        start=offer.starts_at,
        end=offer.ends_at,
        exclude_entry_ids=[entry.id],
    )
    logger.info(
        "offer_declined",
        offer_id=str(offer.id),
        cascaded=next_offer is not None,
    )
    return offer


def _requeue_or_close(entry: WaitlistEntry, *, now: datetime) -> bool:
    """بازگشت به انتهای صف یا بستن درخواست پس از سقف پیشنهادها.

    این تابع تضمین می‌کند صف **قفل نشود**: درخواستی که چند بار پیشنهاد را رد
    کرده، یا به انتهای صف می‌رود یا با وضعیت ``UNFULFILLED`` بسته می‌شود.
    """
    limit = policy_int("waitlist.max_offers_before_requeue")
    if entry.offers_sent >= limit:
        entry.mark(WaitlistStatus.UNFULFILLED, when=now)
        send_sms(
            phone=entry.guest.phone,
            template_key=SmsTemplateKey.WAITLIST_REQUEUED,
            context={
                "window": entry.window_label,
                "offers": fa(entry.offers_sent),
                "full_name": entry.guest.display_name,
            },
            dedup_key=f"waitlist-unfulfilled:{entry.id}",
        )
        return False
    entry.status = WaitlistStatus.WAITING
    entry.requeued_at = now
    entry.save(update_fields=["status", "requeued_at", "updated_at"])
    events.emit(events.ENTRY_REQUEUED, {"entry_id": str(entry.id)})
    return True


def expire_offers(*, now: datetime | None = None) -> int:
    """انقضای پیشنهادهای بی‌پاسخ و آبشار به نفر بعدی؛ بازگشت تعداد منقضی‌شده.

    این تابع هر دقیقه از زمان‌بند صدا زده می‌شود (یا دستی در تست).
    """
    moment = now or timezone.now()
    expired = 0
    stale = list(
        SlotOffer.objects.select_related("entry")
        .filter(status=OfferStatus.OPEN, expires_at__lte=moment)
        .order_by("expires_at")[:200]
    )
    for offer in stale:
        with transaction.atomic():
            locked = SlotOffer.objects.select_for_update().get(pk=offer.pk)
            if locked.status != OfferStatus.OPEN:
                continue
            locked.status = OfferStatus.EXPIRED
            locked.responded_at = moment
            locked.save(update_fields=["status", "responded_at", "updated_at"])
            entry = WaitlistEntry.objects.select_for_update().get(pk=locked.entry_id)
            if entry.status == WaitlistStatus.OFFERED:
                entry.status = WaitlistStatus.EXPIRED
                _requeue_or_close(entry, now=moment)
            audit_record(
                AuditEntry(
                    action=AuditAction.WAITLIST_OFFER_EXPIRED,
                    actor_label="سامانه",
                    target_type="waitlist_entry",
                    target_id=str(entry.id),
                    metadata={"offer_id": str(locked.id), "attempt": entry.offers_sent},
                )
            )
        expired += 1
        offer_freed_slot(
            unit_id=locked.unit_id,
            start=locked.starts_at,
            end=locked.ends_at,
            exclude_entry_ids=[locked.entry_id],
            now=moment,
        )
        events.emit(events.OFFER_EXPIRED, {"offer_id": str(locked.id)})

    if expired:
        logger.info("offers_expired", count=expired)
    return expired


def _invalidate_offer(offer: SlotOffer, *, reason: str) -> None:
    """باطل کردن پیشنهادی که دیگر معتبر نیست (بدون آبشار)."""
    SlotOffer.objects.filter(pk=offer.pk, status=OfferStatus.OPEN).update(
        status=OfferStatus.SUPERSEDED, responded_at=timezone.now()
    )
    logger.info("offer_invalidated", offer_id=str(offer.id), reason=reason)


def _slot_is_bookable(*, unit_id: Any, start: datetime, end: datetime) -> bool:
    """آیا سانس همین حالا آزاد و قابل رزرو است؟"""
    from apps.reservation.availability import is_unit_free

    return is_unit_free(unit_id=unit_id, start=start, end=end)


def _free_unit_for(*, start: datetime, end: datetime) -> Any | None:
    """نخستین واحد آزاد در بازه (برای راهنمایی زائر به رزرو مستقیم)."""
    from apps.reservation.availability import free_units_for

    units = free_units_for(start=start, end=end)
    return units[0] if units else None


# ─── آمار صف (برای تابلوی کارکنان و داشبورد) ────────────────────────────────
def queue_stats() -> dict[str, Any]:
    """خلاصهٔ وضعیت صف — برای کارت‌های تابلوی پذیرش."""
    now = timezone.now()
    aggregate = WaitlistEntry.objects.aggregate(
        waiting=Count("id", filter=Q(status=WaitlistStatus.WAITING)),
        offered=Count("id", filter=Q(status=WaitlistStatus.OFFERED)),
        converted=Count("id", filter=Q(status=WaitlistStatus.CONVERTED)),
        unfulfilled=Count("id", filter=Q(status=WaitlistStatus.UNFULFILLED)),
    )
    open_offers = SlotOffer.objects.filter(status=OfferStatus.OPEN)
    expiring_soon = open_offers.filter(expires_at__lte=now + timedelta(minutes=10)).count()
    return {
        **aggregate,
        "open_offers": open_offers.count(),
        "expiring_soon": expiring_soon,
        "next_offer": open_offers.order_by("expires_at").first(),
        "acceptance_rate": _acceptance_rate(),
    }


def _acceptance_rate() -> float:
    """نرخ پذیرش پیشنهادها (۰ تا ۱) — شاخص سلامت صف."""
    total = SlotOffer.objects.exclude(status=OfferStatus.SUPERSEDED).count()
    if not total:
        return 0.0
    accepted = SlotOffer.objects.filter(status=OfferStatus.ACCEPTED).count()
    return round(accepted / total, 4)


def slot_free_handler(payload: Mapping[str, Any]) -> int:
    """مشترک رخداد ``slot.freed`` — پل میان دامنهٔ رزرو و صف انتظار."""
    unit_id = payload.get("unit_id")
    start_raw = payload.get("start")
    end_raw = payload.get("end")
    if not (unit_id and start_raw and end_raw):
        return 0
    start = datetime.fromisoformat(str(start_raw))
    end = datetime.fromisoformat(str(end_raw))
    offer = offer_freed_slot(
        unit_id=unit_id,
        start=start,
        end=end,
        reservation_id=payload.get("reservation_id"),
    )
    return 1 if offer else 0
