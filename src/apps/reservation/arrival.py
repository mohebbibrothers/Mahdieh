"""هماهنگی ورود پس از ثبت — ساعت تقریبی رسیدن، پلاک خودرو و یادداشت برای پذیرش.

زائر این سه را هنگام ثبت می‌دهد، اما برنامهٔ سفر عوض می‌شود (قطار تأخیر دارد، خودرو عوض
شد، کالسکهٔ کودک همراه است). این ماژول **تنها راه** به‌روزرسانی آن‌ها پس از ثبت است:

* فقط صاحب رزرو (یا کارکنان با ``is_staff``) و فقط پیش از پذیرش حضوری — یعنی در وضعیت
  «نگه‌داشت/تأییدشده» و پیش از پایان بازه؛ پس از پذیرش، این داده سند پیشخوان است.
* هیچ اثری بر سانس، ظرفیت یا سهمیه ندارد ⇒ قفل واحد و بازمحاسبهٔ موجودی لازم نیست؛
  فقط قفل ردیف رزرو تا دو ویرایش هم‌زمان یکدیگر را نیمه‌کاره بازنویسی نکنند.
* بی‌تغییر ⇒ بی‌رخداد (دوبار زدن «ثبت» تاریخچه را شلوغ نمی‌کند).
* در تاریخچه و ممیزی فقط **نام فیلدهای** تغییرکرده ثبت می‌شود، نه مقدارها (پلاک دادهٔ
  شخصی است و ممیزی زنجیره‌ای پاک‌شدنی نیست).
"""

from __future__ import annotations

from datetime import time
from typing import Any, Final

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.errors import ConflictError, PermissionDeniedError
from apps.identity.models import User
from apps.reservation.choices import ReservationEventKind, ReservationStatus
from apps.reservation.models import Reservation

#: فیلدهای قابل ویرایش و سقف طول هر کدام (هم‌سان با ستون مدل و ورودی ثبت)
EDITABLE_FIELDS: Final[tuple[str, ...]] = ("expected_arrival", "vehicle_plate", "guest_note")
PLATE_MAX: Final = 20
NOTE_MAX: Final = 300


def details_editable(reservation: Reservation) -> bool:
    """آیا زائر هنوز می‌تواند هماهنگی ورود را تغییر دهد؟ (یک قاعده برای API و نمایش دکمه)"""
    return (
        reservation.status in ReservationStatus.cancellable_statuses()
        and reservation.ends_at > timezone.now()
    )


def _clean_text(value: Any, limit: int) -> str:
    """فاصله‌های اضافه و نویسه‌های کنترلی حذف، سپس برش به سقف ستون."""
    text = " ".join(str(value or "").split())
    return text[:limit]


def _normalized(changes: dict[str, Any]) -> dict[str, Any]:
    """فقط فیلدهای مجاز، با پاک‌سازی یکسانِ مسیر ثبت."""
    clean: dict[str, Any] = {}
    if "expected_arrival" in changes:
        arrival = changes["expected_arrival"]
        clean["expected_arrival"] = arrival if isinstance(arrival, time) else None
    if "vehicle_plate" in changes:
        clean["vehicle_plate"] = _clean_text(changes["vehicle_plate"], PLATE_MAX)
    if "guest_note" in changes:
        # یادداشت چندخطی است؛ فقط لبه‌ها پیراسته می‌شوند تا شکست خط زائر بماند.
        clean["guest_note"] = str(changes["guest_note"] or "").strip()[:NOTE_MAX]
    return clean


def update_arrival_details(
    *,
    reservation: Reservation,
    by: User,
    changes: dict[str, Any],
    is_staff: bool = False,
    request: HttpRequest | None = None,
) -> Reservation:
    """به‌روزرسانی ساعت رسیدن، پلاک و یادداشت پذیرش؛ بازگرداندن رزرو تازه.

    خطاها: ``PermissionDeniedError`` (رزرو دیگری) و ``ConflictError`` با کد
    ``details_locked`` (پس از پذیرش، لغو یا پایان اقامت).
    """
    if not is_staff and reservation.guest_id != by.id:
        raise PermissionDeniedError("ویرایش رزرو دیگران مجاز نیست.")
    clean = _normalized(changes)

    with transaction.atomic():
        locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if not details_editable(locked):
            raise ConflictError(
                "هماهنگی ورود فقط پیش از پذیرش حضوری قابل تغییر است؛ "
                "برای تغییر، با پذیرش زائرسرا تماس بگیرید.",
                code="details_locked",
            )
        changed = [name for name, value in clean.items() if getattr(locked, name) != value]
        if not changed:
            return reservation
        for name in changed:
            setattr(locked, name, clean[name])
        locked.save(update_fields=[*changed, "updated_at"])
        locked.publish_event(ReservationEventKind.DETAILS, actor=by, metadata={"fields": changed})
        audit_record(
            AuditEntry(
                action=AuditAction.RESERVATION_DETAILS_UPDATED,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="reservation",
                target_id=str(locked.id),
                metadata={"code": locked.code, "fields": changed, "by_staff": is_staff},
                request=request,
            )
        )
    # واحد/طبقه/مهمان از رکورد قفل‌شده بارگذاری نشده‌اند؛ نمای پاسخ همان پیش‌بارگذاری قبلی را می‌خواهد.
    for name in changed:
        setattr(reservation, name, clean[name])
    reservation.updated_at = locked.updated_at
    return reservation
