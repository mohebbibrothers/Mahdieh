"""سرویس‌های رزرو — تنها مسیر مجاز انتقال حالت.

اصل طراحی: **اعتبارسنجی پیش از تصادم، تصادم پیش از نوشتن**.
    ۱. پیش از هر نوشتن، ``collect_violations`` همهٔ قواعد سیاست (I2–I11) را
       جمع می‌کند — نه یکی‌یکی و نه با اولین خطا. نتیجه: کاربر به‌جای پنج
       تلاش، یک فهرست کامل از دلایل می‌بیند.
    ۲. اگر بازه اشغال باشد، پیش از هر خطای دیتابیس، «بازه‌های جایگزین» برمی‌گردد.
    ۳. اگر با این حال دو درخواست هم‌زمان از صافی رد شوند، قید ``EXCLUDE`` دیتابیس
       یکی را رد می‌کند و خطای خام به پیام دامنه‌ای + جایگزین ترجمه می‌شود.

هر انتقال: رخداد رزرو + Outbox (هم‌تراکنش) + حسابرسی + ابطال کش + پیامک.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone
from psycopg.types.range import Range

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import events, metrics, outbox
from apps.core.errors import (
    ConflictError,
    PermissionDeniedError,
    PolicyViolationError,
    ReservationConflictError,
    StaleResourceError,
)
from apps.core.jalali import clock_label, duration_days_label, format_jalali
from apps.core.text import to_persian_digits as fa
from apps.identity.models import User
from apps.inventory.lattice import LatticeConfig, count_cells, is_on_grid, with_cleaning_buffer
from apps.inventory.models import Unit
from apps.notifications.choices import SmsTemplateKey
from apps.notifications.services import send_sms
from apps.reservation import allocation, selectors, standing
from apps.reservation.availability import alternative_periods, invalidate_availability
from apps.reservation.choices import (
    CancelReasonKind,
    ReservationEventKind,
    ReservationSource,
    ReservationStatus,
)
from apps.reservation.codes import next_reservation_code
from apps.reservation.models import Reservation
from apps.reservation.policy import lattice_config, policy_bool, policy_int

logger = logging.getLogger(__name__)

REASON_MIN_LENGTH = 5
MAX_GUESTS_PER_RESERVATION = 60
ALTERNATIVE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class Quote:
    """نتیجهٔ پیش‌بینی یک درخواست رزرو — «قبل از تأیید، همه‌چیز روشن»."""

    allowed: bool
    violations: tuple[dict[str, str], ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    unit_code: str = ""
    unit_title: str = ""
    start: datetime | None = None
    end: datetime | None = None
    cells: int = 0
    duration_label: str = ""
    guests_count: int = 0
    capacity_total: int = 0
    is_available: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر برای پاسخ API."""
        return {
            "allowed": self.allowed,
            "is_available": self.is_available,
            "violations": list(self.violations),
            "alternatives": list(self.alternatives),
            "unit": {"code": self.unit_code, "title": self.unit_title},
            "start": timezone.localtime(self.start).isoformat() if self.start else None,
            "end": timezone.localtime(self.end).isoformat() if self.end else None,
            "cells": self.cells,
            "duration_label": self.duration_label,
            "guests_count": self.guests_count,
            "capacity_total": self.capacity_total,
            "notes": list(self.notes),
        }


def _violation(code: str, message: str) -> dict[str, str]:
    """ساخت یک تخلف ساختاریافته (کد ماشین‌خوان + پیام انسانی).

    پیام انسانی همیشه با ارقام فارسی برمی‌گردد (ADR-0014): عددهایی مثل سقف
    ظرفیت یا افق رزرو در f-string ساخته می‌شوند و بدون این گذر، «۲ ساعت» به
    شکل «2 ساعت» به چشم زائر می‌رسید. کد ماشین‌خوان دست نمی‌خورد.
    """
    return {"code": code, "message": fa(message)}


def cleaning_buffer_for(unit: Unit) -> int:
    """بافر نظافت مؤثر یک واحد (دقیقه) — بازنویسی واحد، وگرنه سیاست سراسری.

    ``None`` روی واحد یعنی «تابع سیاست»؛ صفر یعنی «صریحاً بی‌بافر». پیش‌تر
    ``unit.value or policy`` صفرِ صریح را با «تعیین‌نشده» یکی می‌گرفت.
    """
    if unit.cleaning_buffer_minutes is not None:
        return int(unit.cleaning_buffer_minutes)
    return policy_int("lattice.default_cleaning_buffer_minutes")


def _violations_for_period(
    *,
    unit: Unit,
    start: datetime,
    end: datetime,
    guests_count: int,
    cfg: LatticeConfig,
) -> tuple[list[dict[str, str]], int]:
    """تخلف‌های مربوط به بازه، واحد و ظرفیت + تعداد نیم‌روز محاسبه‌شده.

    خروجی: ``(violations, cells)`` — تعداد نیم‌روز برای استفاده در سهمیه‌ها.
    """
    violations: list[dict[str, str]] = []
    now = timezone.now()

    if not unit.is_bookable:
        violations.append(_violation("unit_inactive", "این واحد در حال حاضر قابل‌رزرو نیست."))
    if guests_count < 1 or guests_count > MAX_GUESTS_PER_RESERVATION:
        violations.append(_violation("guests_invalid", "تعداد نفرات باید حداقل ۱ نفر باشد."))
    elif guests_count > unit.capacity_total:
        violations.append(
            _violation(
                "capacity_exceeded",
                f"ظرفیت این {unit.type_label} حداکثر {unit.capacity_total} نفر است.",
            )
        )
    if end <= start:
        violations.append(_violation("invalid_period", "پایان بازه باید بعد از آغاز آن باشد."))
        return violations, 0

    on_grid = is_on_grid(start, end, cfg)
    if not on_grid and not policy_bool("reservation.allow_off_grid"):
        violations.append(
            _violation(
                "off_grid",
                "زمان انتخابی روی مرزهای نیم‌روز نیست؛ بازه‌های ۰۸:۰۰ و ۲۰:۰۰ را انتخاب کنید.",
            )
        )

    cells = 0
    if on_grid:
        cells = count_cells(start, end, cfg)
        min_cells = policy_int("reservation.min_cells")
        max_cells = policy_int("reservation.max_cells")
        if cells < min_cells or cells > max_cells:
            violations.append(
                _violation(
                    "cells_out_of_range",
                    f"مدت اقامت باید بین {min_cells} و {max_cells} نیم‌روز "
                    "(بین نصف روز و یک هفته) باشد.",
                )
            )

    lead_hours = policy_int("reservation.min_lead_time_hours")
    horizon_days = policy_int("reservation.booking_horizon_days")
    if start <= now:
        violations.append(_violation("in_past", "امکان رزرو برای زمان گذشته وجود ندارد."))
    elif start < now + timedelta(hours=lead_hours):
        violations.append(
            _violation(
                "too_soon",
                f"برای رزرو، حداقل {lead_hours} ساعت پیش از زمان ورود فرصت لازم است.",
            )
        )
    if start > now + timedelta(days=horizon_days):
        violations.append(
            _violation(
                "beyond_horizon",
                f"رزرو تا {horizon_days} روز آینده امکان‌پذیر است؛ "
                "برای تاریخ‌های دورتر با پذیرش تماس بگیرید.",
            )
        )
    return violations, cells


def _violations_for_guest(
    *, guest: User, start: datetime, cells: int, exclude_pk: UUID | None
) -> list[dict[str, str]]:
    """تخلف‌های مربوط به سهمیه‌ها، خنک‌شدن، محرومیت و فاصلهٔ بین رزروها."""
    violations: list[dict[str, str]] = []
    now = timezone.now()

    max_active = policy_int("reservation.max_active_per_user")
    active = selectors.active_count(guest, exclude_pk=exclude_pk)
    if active >= max_active:
        violations.append(
            _violation(
                "active_limit",
                f"شما {active} رزرو فعال دارید و سقف مجاز {max_active} است؛ "
                "برای رزرو تازه، رزرو فعلی را لغو کنید.",
            )
        )

    window_days = policy_int("reservation.cells_window_days")
    max_window = policy_int("reservation.max_cells_per_window")
    used_window = selectors.cells_used(
        guest=guest, start=now - timedelta(days=window_days), end=now, exclude_pk=exclude_pk
    )
    if cells and used_window + cells > max_window:
        violations.append(
            _violation(
                "window_quota",
                f"سهمیهٔ شما در {window_days} روز گذشته تکمیل شده است "
                f"({used_window} از {max_window} نیم‌روز).",
            )
        )

    max_year = policy_int("reservation.max_cells_per_year")
    used_year = selectors.cells_used(
        guest=guest, start=now - timedelta(days=365), end=now, exclude_pk=exclude_pk
    )
    if cells and used_year + cells > max_year:
        violations.append(
            _violation(
                "year_quota",
                f"سهمیهٔ سالانهٔ شما ({used_year} از {max_year} نیم‌روز) اجازهٔ این رزرو را نمی‌دهد.",
            )
        )

    cooldown_until = selectors.pending_cooldown_until(
        guest, hours=policy_int("reservation.late_cancel_cooldown_hours")
    )
    if cooldown_until is not None:
        violations.append(
            _violation(
                "cooldown",
                "پس از لغو دیرهنگام، مدت کوتاهی امکان رزرو تازه نیست. "
                f"زمان آزاد شدن: {_format_moment(cooldown_until)}",
            )
        )

    suspended_until = selectors.suspended_until(
        guest, days=policy_int("reservation.no_show_suspension_days")
    )
    if suspended_until is not None:
        violations.append(
            _violation(
                "suspended",
                "حساب شما به‌خاطر عدم‌حضور قبلی موقتاً محدود شده است. "
                f"پایان محدودیت: {_format_moment(suspended_until)}",
            )
        )

    gap_hours = policy_int("reservation.min_gap_between_bookings_hours")
    last_end = selectors.last_reservation_end(guest, before=start, exclude_pk=exclude_pk)
    if last_end is not None and gap_hours and start < last_end + timedelta(hours=gap_hours):
        violations.append(
            _violation("min_gap", f"بین دو رزرو متوالی باید حداقل {gap_hours} ساعت فاصله باشد.")
        )
    return violations


def collect_violations(
    *,
    guest: User,
    unit: Unit,
    start: datetime,
    end: datetime,
    guests_count: int,
    config: LatticeConfig | None = None,
    exclude_reservation: Reservation | None = None,
    require_profile: bool = True,
) -> list[dict[str, str]]:
    """جمع‌آوری **همهٔ** تخلف‌های قواعد رزرو برای یک درخواست.

    ``require_profile`` تنها در مسیر تلفنی/پذیرش ``False`` می‌شود: کارمند در
    پذیرش، هویت زائر را حضوری احراز می‌کند و بندکردن رزرو به تکمیل کد ملی در
    آن لحظه، فقط صف پشت پیشخوان می‌سازد (استثنای مستند، نه سهل‌انگاری).

    تفکیک داخلی: تخلف‌های «بازه/واحد/ظرفیت» و تخلف‌های «سهمیهٔ کاربر». این
    تفکیک باعث می‌شود هر گروه کوچک، خوانا و مستقل قابل تست باشد.
    """
    cfg = config or lattice_config()
    period_violations, cells = _violations_for_period(
        unit=unit, start=start, end=end, guests_count=guests_count, cfg=cfg
    )
    violations = list(period_violations)
    if require_profile and not guest.is_profile_complete:
        violations.append(
            _violation("profile_incomplete", "برای رزرو ابتدا پروفایل خود را کامل کنید.")
        )
    violations.extend(
        _violations_for_guest(
            guest=guest,
            start=start,
            cells=cells,
            exclude_pk=exclude_reservation.pk if exclude_reservation else None,
        )
    )
    return violations


def _format_moment(moment: datetime) -> str:
    """قالب‌بندی فارسی تاریخ و ساعت برای پیام کاربر و پیامک."""
    local = timezone.localtime(moment)
    return f"{format_jalali(local.date(), numeric=True)} ساعت {clock_label(local)}"


def build_quote(
    *,
    unit: Unit,
    start: datetime,
    end: datetime,
    guests_count: int,
    guest: User,
    config: LatticeConfig | None = None,
) -> Quote:
    """پیش‌بینی کامل یک رزرو **پیش از** هر نوشتن (سبد رزرو فاز ۳)."""
    cfg = config or lattice_config()
    violations = collect_violations(
        guest=guest, unit=unit, start=start, end=end, guests_count=guests_count, config=cfg
    )
    cells = count_cells(start, end, cfg) if is_on_grid(start, end, cfg) else 0
    conflicts = selectors.active_conflicts(unit=unit, start=start, end=end)
    is_available = not conflicts.exists()
    alternatives: tuple[dict[str, Any], ...] = ()
    if not is_available:
        alternatives = tuple(
            alternative_periods(
                unit=unit, start=start, end=end, config=cfg, limit=ALTERNATIVE_LIMIT
            )
        )
        violations.append(_violation("not_available", "این بازه در همین لحظه اشغال است."))
    notes: tuple[str, ...] = (
        f"ظرفیت: {unit.capacity_total} نفر · واحد: {unit.title}",
        "رزرو در مهدیه کاملاً رایگان است و درگاه پرداخت ندارد.",
    )
    return Quote(
        allowed=not violations,
        violations=tuple(violations),
        alternatives=alternatives,
        unit_code=unit.code,
        unit_title=unit.title,
        start=start,
        end=end,
        cells=cells,
        duration_label=_duration_label(cells),
        guests_count=guests_count,
        capacity_total=unit.capacity_total,
        is_available=is_available,
        notes=notes,
    )


def _duration_label(cells: int) -> str:
    """برچسب فارسی مدت اقامت از تعداد نیم‌روز."""
    from apps.inventory.lattice import stay_label

    return stay_label(cells) if cells else ""


def _conflict_error(*, unit: Unit, start: datetime, end: datetime) -> ReservationConflictError:
    """ساخت خطای تصادم همراه با بازه‌های جایگزین پیشنهادی."""
    alternatives = alternative_periods(unit=unit, start=start, end=end, limit=ALTERNATIVE_LIMIT)
    metrics.RESERVATION_CONFLICTS.labels(unit_code=unit.code).inc()
    return ReservationConflictError(
        "متأسفانه در همین لحظه این بازهٔ زمانی رزرو شد. چند بازهٔ آزاد برای شما پیدا کردیم.",
        extensions={"alternatives": alternatives, "unit_code": unit.code},
    )


def _sms_context(reservation: Reservation) -> dict[str, Any]:
    """زمینهٔ مشترک پیامک‌های رزرو — یک جا، تا هیچ فراخوانی متغیری جا نگذارد.

    الگوهای رسمی پنل پیامک فقط چند متغیر می‌پذیرند («name» و «rooz» و «date»)،
    پس زمینه باید همان‌ها را **همیشه** داشته باشد؛ وگرنه ارسال مسدود می‌شود
    (قاعدهٔ «هرگز پیام درهم» در ``apps/notifications/patterns.py``).
    """
    return {
        "code": reservation.code,
        "unit": reservation.unit.title,
        "check_in": _format_moment(reservation.starts_at),
        "check_out": _format_moment(reservation.ends_at),
        "guests": reservation.guests_count,
        "full_name": reservation.guest.display_name,
        "rooz": duration_days_label(reservation.starts_at, reservation.ends_at),
        "date": _format_moment(reservation.starts_at),
    }


def _send_confirmation(reservation: Reservation) -> None:
    """پیامک تأیید رزرو — بیرون از تراکنش (شبکه هرگز قفل دیتابیس را نگه نمی‌دارد)."""
    send_sms(
        phone=reservation.guest.phone,
        template_key=SmsTemplateKey.BOOKING_CONFIRMED,
        context=_sms_context(reservation),
        dedup_key=f"confirm:{reservation.id}",
    )


def create_reservation(
    *,
    guest: User,
    unit: Unit,
    start: datetime,
    end: datetime,
    guests_count: int,
    require_profile: bool = True,
    source: str = ReservationSource.WEB,
    actor: User | None = None,
    guest_note: str = "",
    staff_note: str = "",
    expected_arrival: time | None = None,
    vehicle_plate: str = "",
    request: HttpRequest | None = None,
) -> Reservation:
    """ثبت رزرو تازه — با تمام نگهبان‌ها و تضمین دیتابیسی عدم‌همپوشانی."""
    cfg = lattice_config()
    violations = collect_violations(
        guest=guest,
        unit=unit,
        start=start,
        end=end,
        guests_count=guests_count,
        config=cfg,
        require_profile=require_profile,
    )
    if violations:
        raise PolicyViolationError(violations[0]["message"], extensions={"violations": violations})

    cells = count_cells(start, end, cfg)
    # قاعدهٔ تخصیص ایام پیک (پیش‌فرض خاموش): اگر مدیر برای این بازه قاعده‌ای
    # روشن کرده باشد، سقف نیم‌روز این زائر کم می‌شود — با پیام شفاف، نه خطای خشک.
    peak_limit = allocation.peak_limit_for(guest=guest, start=start, end=end)
    if peak_limit is not None and cells > peak_limit:
        raise PolicyViolationError(
            (
                f"در این ایام پرتقاضا، سهم هر زائر {fa(peak_limit)} نیم‌روز است؛ "
                f"بازهٔ درخواستی شما {fa(cells)} نیم‌روز می‌شود. بازهٔ کوتاه‌تری انتخاب کنید."
            ),
            extensions={
                "violations": [_violation("peak_limit", f"سقف ایام پیک: {fa(peak_limit)} نیم‌روز")]
            },
        )

    block_start, block_end = with_cleaning_buffer(start, end, minutes=cleaning_buffer_for(unit))
    holds_enabled = policy_bool("reservation.holds_enabled")
    hold_minutes = policy_int("reservation.hold_ttl_minutes")

    with transaction.atomic():
        # قفل ردیف کاربر: سهمیه‌ها در هم‌زمانی قابل دور زدن نیستند.
        User.objects.select_for_update().filter(pk=guest.pk).first()

        # سهمیهٔ کاربر دوباره **زیر قفل** بررسی می‌شود؛ بررسی پیشین (برای پیام
        # دوستانه) بدون قفل انجام شد و در هم‌زمانی قابل دور زدن بود.
        quota_violations = _violations_for_guest(
            guest=guest, start=start, cells=cells, exclude_pk=None
        )
        if quota_violations:
            raise PolicyViolationError(
                quota_violations[0]["message"], extensions={"violations": quota_violations}
            )

        existing = selectors.active_conflicts(unit=unit, start=start, end=end)
        if existing.exists():
            raise _conflict_error(unit=unit, start=start, end=end)

        # ``atomic`` تودرتو = savepoint؛ بدون آن، خطای قید ``EXCLUDE`` کل
        # تراکنش را «مسموم» می‌کند و ساخت بازه‌های جایگزین (که خودش کوئری است)
        # با TransactionManagementError می‌شکند ⇒ ۵۰۰ روی همان مسیر مسابقه.
        try:
            with transaction.atomic():
                reservation = Reservation.objects.create(
                    code=next_reservation_code(),
                    unit=unit,
                    guest=guest,
                    stay_period=Range(start, end),
                    block_period=Range(block_start, block_end),
                    cells_count=cells,
                    guests_count=guests_count,
                    status=ReservationStatus.HOLD if holds_enabled else ReservationStatus.CONFIRMED,
                    source=source,
                    created_by=actor,
                    guest_note=guest_note[:300],
                    staff_note=staff_note[:300],
                    expected_arrival=expected_arrival,
                    vehicle_plate=vehicle_plate[:20],
                    hold_expires_at=(
                        timezone.now() + timedelta(minutes=hold_minutes) if holds_enabled else None
                    ),
                )
        except IntegrityError as exc:
            # مسابقهٔ واقعی: قید EXCLUDE دیتابیس درست عمل کرد.
            raise _conflict_error(unit=unit, start=start, end=end) from exc

        reservation.publish_event(
            ReservationEventKind.CREATED,
            to_status=reservation.status,
            actor=actor or guest,
            metadata={"source": source, "cells": cells},
        )
        outbox.publish(
            "reservation.confirmed",
            aggregate_type="reservation",
            aggregate_id=reservation.id,
            payload={
                "reservation_id": str(reservation.id),
                "code": reservation.code,
                "unit_code": unit.code,
                "guest_id": str(guest.id),
                "cells_count": cells,
                "guests_count": guests_count,
                "source": source,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.RESERVATION_CREATED,
                actor_id=(actor or guest).id,
                actor_label=(actor or guest).display_name,
                target_type="reservation",
                target_id=str(reservation.id),
                metadata={
                    "code": reservation.code,
                    "unit": unit.code,
                    "cells": cells,
                    "guests": guests_count,
                    "source": source,
                },
                request=request,
            )
        )
        invalidate_availability()

    metrics.RESERVATION_EVENTS.labels(event="created").inc()
    logger.info(
        "reservation_created",
        extra={"code": reservation.code, "unit": unit.code, "cells": cells},
    )
    _send_confirmation(reservation)
    return reservation


def cancel_reservation(
    *,
    reservation: Reservation,
    by: User,
    reason_kind: str = CancelReasonKind.GUEST_REQUEST,
    reason: str = "",
    is_staff: bool = False,
    request: HttpRequest | None = None,
) -> Reservation:
    """لغو رزرو و آزادسازی فوری سانس."""
    if not is_staff and reservation.guest_id != by.id:
        raise PermissionDeniedError("لغو رزرو دیگران مجاز نیست.")
    if is_staff and len(reason.strip()) < REASON_MIN_LENGTH:
        raise PolicyViolationError(
            "ثبت دلیل برای لغو توسط کارکنان اجباری است (حداقل ۵ نویسه).",
            extensions={"violations": [_violation("reason_required", "دلیل لغو را وارد کنید.")]},
        )
    if reservation.status not in ReservationStatus.cancellable_statuses():
        raise ConflictError(
            f"این رزرو در وضعیت «{reservation.status_label()}» قابل لغو نیست.",
            code="not_cancellable",
        )

    # یک قاعده، یک پیاده‌سازی: پیش‌نمایش لغو (API) هم از همین تابع می‌پرسد.
    late = not is_staff and standing.is_late_cancel(reservation)

    with transaction.atomic():
        locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if locked.status not in ReservationStatus.cancellable_statuses():  # pragma: no cover
            raise ConflictError("این رزرو هم‌اکنون لغو شد.", code="already_cancelled")
        previous = locked.status
        locked.status = (
            ReservationStatus.CANCELLED_BY_STAFF
            if is_staff
            else ReservationStatus.CANCELLED_BY_GUEST
        )
        locked.cancelled_at = timezone.now()
        locked.cancelled_by = by
        locked.cancel_reason_kind = reason_kind
        locked.cancel_reason = reason[:300]
        locked.is_late_cancel = late
        locked.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason_kind",
                "cancel_reason",
                "is_late_cancel",
                "updated_at",
            ]
        )
        locked.publish_event(
            ReservationEventKind.CANCELLED,
            from_status=previous,
            to_status=locked.status,
            actor=by,
            reason=reason,
            metadata={"late": late, "reason_kind": reason_kind},
        )
        outbox.publish(
            "reservation.cancelled",
            aggregate_type="reservation",
            aggregate_id=locked.id,
            payload={
                "reservation_id": str(locked.id),
                "code": locked.code,
                "unit_code": locked.unit.code,
                "cancelled_by": str(by.id),
                "late": late,
                "reason_kind": reason_kind,
                "start": locked.starts_at.isoformat(),
                "end": locked.ends_at.isoformat(),
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.RESERVATION_CANCELLED,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="reservation",
                target_id=str(locked.id),
                reason=reason or "درخواست زائر",
                metadata={"code": locked.code, "late": late, "by_staff": is_staff},
                request=request,
            )
        )
        invalidate_availability()

    metrics.RESERVATION_EVENTS.labels(event="cancelled").inc()
    _announce_freed_slot(
        unit=locked.unit,
        start=locked.starts_at,
        end=locked.ends_at,
        cause="cancelled",
        reservation_id=locked.id,
    )
    send_sms(
        phone=locked.guest.phone,
        template_key=SmsTemplateKey.BOOKING_CANCELLED,
        context={"code": locked.code, "full_name": locked.guest.display_name},
        dedup_key=f"cancel:{locked.id}",
    )
    return locked


def _announce_freed_slot(
    *,
    unit: Unit,
    start: datetime,
    end: datetime,
    cause: str,
    reservation_id: Any | None = None,
) -> None:
    """اطلاع «سانس آزاد شد» به بقیهٔ ماژول‌ها (صف انتظار و هر مصرف‌کنندهٔ آینده).

    این خط، مرز وابستگی را نگه می‌دارد: دامنهٔ رزرو نمی‌داند صف انتظار وجود
    دارد؛ فقط رخداد را منتشر می‌کند و اگر شنونده‌ای شکست بخورد، لغو/جابه‌جایی
    کاربر هرگز آسیب نمی‌بیند (``emit`` استثناها را ایزوله می‌کند).
    """
    events.emit(
        events.SLOT_FREED,
        {
            "unit_id": str(unit.id),
            "unit_code": unit.code,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "cause": cause,
            "reservation_id": str(reservation_id) if reservation_id else None,
        },
    )


def move_reservation(
    *,
    reservation: Reservation,
    unit: Unit,
    start: datetime,
    end: datetime,
    by: User,
    reason: str,
    expected_version: int | None = None,
    request: HttpRequest | None = None,
) -> Reservation:
    """جابه‌جایی رزرو (سناریوی S4) — رزرو قدیم جانشین می‌شود، سابقه می‌ماند."""
    if not by.is_staff_member:
        raise PermissionDeniedError("جابه‌جایی رزرو فقط توسط کارکنان مجاز است.")
    if len(reason.strip()) < REASON_MIN_LENGTH:
        raise PolicyViolationError("ثبت دلیل برای جابه‌جایی اجباری است.")
    if reservation.status not in (ReservationStatus.CONFIRMED, ReservationStatus.HOLD):
        raise ConflictError(
            f"رزرو در وضعیت «{reservation.status_label()}» قابل جابه‌جایی نیست.",
            code="not_movable",
        )
    if expected_version is not None and expected_version != reservation.version:
        raise StaleResourceError(
            "این رزرو توسط کاربر دیگری تغییر کرده است؛ تابلوی خود را بازخوانی کنید.",
            extensions={"current_version": reservation.version},
        )

    cfg = lattice_config()
    violations = collect_violations(
        guest=reservation.guest,
        unit=unit,
        start=start,
        end=end,
        guests_count=reservation.guests_count,
        config=cfg,
        exclude_reservation=reservation,
    )
    if violations:
        raise PolicyViolationError(violations[0]["message"], extensions={"violations": violations})

    cells = count_cells(start, end, cfg)
    block_start, block_end = with_cleaning_buffer(start, end, minutes=cleaning_buffer_for(unit))

    # سانسِ «قبل از جابه‌جایی» را نگه می‌داریم تا پس از کامیت، آزادشدن آن را
    # به صف انتظار اطلاع دهیم (پیش از این خط، رزرو تغییر نکرده است).
    old_unit = reservation.unit
    old_start = reservation.starts_at
    old_end = reservation.ends_at
    old_id = reservation.id

    with transaction.atomic():
        locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if selectors.active_conflicts(
            unit=unit, start=start, end=end, exclude_pk=locked.pk
        ).exists():
            raise _conflict_error(unit=unit, start=start, end=end)
        try:
            replacement = Reservation.objects.create(
                code=next_reservation_code(),
                unit=unit,
                guest=locked.guest,
                stay_period=Range(start, end),
                block_period=Range(block_start, block_end),
                cells_count=cells,
                guests_count=locked.guests_count,
                status=ReservationStatus.CONFIRMED,
                source=locked.source,
                created_by=by,
                guest_note=locked.guest_note,
                staff_note=f"جابه‌جایی از {locked.code}: {reason.strip()}"[:300],
                expected_arrival=locked.expected_arrival,
                vehicle_plate=locked.vehicle_plate,
            )
        except IntegrityError as exc:
            raise _conflict_error(unit=unit, start=start, end=end) from exc

        previous_status = locked.status
        locked.status = ReservationStatus.SUPERSEDED
        locked.superseded_by = replacement
        locked.save(update_fields=["status", "superseded_by", "updated_at"])
        locked.publish_event(
            ReservationEventKind.MOVED,
            from_status=previous_status,
            to_status=ReservationStatus.SUPERSEDED,
            actor=by,
            reason=reason.strip(),
            metadata={"new_code": replacement.code, "unit": unit.code},
        )
        replacement.publish_event(
            ReservationEventKind.CREATED,
            to_status=ReservationStatus.CONFIRMED,
            actor=by,
            reason=f"جابه‌جایی از {locked.code}",
            metadata={"source": "move", "cells": cells},
        )
        outbox.publish(
            "reservation.superseded",
            aggregate_type="reservation",
            aggregate_id=locked.id,
            payload={
                "old_id": str(locked.id),
                "old_code": locked.code,
                "new_id": str(replacement.id),
                "new_code": replacement.code,
                "unit_code": unit.code,
                "moved_by": str(by.id),
                "reason": reason.strip(),
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.RESERVATION_MOVED,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="reservation",
                target_id=str(locked.id),
                reason=reason.strip(),
                metadata={
                    "old_code": locked.code,
                    "new_code": replacement.code,
                    "unit": unit.code,
                    "start": start.isoformat(),
                },
                request=request,
            )
        )
        invalidate_availability()

    metrics.RESERVATION_EVENTS.labels(event="moved").inc()
    _announce_freed_slot(
        unit=old_unit,
        start=old_start,
        end=old_end,
        cause="moved",
        reservation_id=old_id,
    )
    # جابجایی، «تغییر» است نه «رزرو تازه»: قالب اختصاصی خودش را دارد.
    send_sms(
        phone=replacement.guest.phone,
        template_key=SmsTemplateKey.BOOKING_MOVED,
        context=_sms_context(replacement),
        dedup_key=f"move:{replacement.id}",
    )
    return replacement


def check_in(
    *, reservation: Reservation, by: User, request: HttpRequest | None = None
) -> Reservation:
    """پذیرش مهمان — انتقال ``confirmed`` به ``checked_in``."""
    if not by.is_staff_member:
        raise PermissionDeniedError("پذیرش فقط توسط کارکنان انجام می‌شود.")
    if reservation.status != ReservationStatus.CONFIRMED:
        raise ConflictError(
            f"پذیرش برای رزرو در وضعیت «{reservation.status_label()}» معنا ندارد.",
            code="not_checkable",
        )
    now = timezone.now()
    if now > reservation.ends_at:
        raise ConflictError("بازهٔ این رزرو پایان یافته است.", code="period_over")
    early_hours = policy_int("reservation.checkin_early_hours")
    opens_at = reservation.starts_at - timedelta(hours=early_hours)
    if now < opens_at:
        # یافتهٔ H3: پیش‌تر پذیرشِ «فردا» هم ممکن بود و رزروِ پیش رو، پیش از موعد
        # «در حال اقامت» می‌شد (تقویم و گزارش اشغال نادرست می‌شدند).
        raise ConflictError(
            fa(
                f"پذیرش این رزرو از {_format_moment(opens_at)} ممکن است "
                f"({early_hours} ساعت پیش از آغاز اقامت)."
            ),
            code="too_early_to_check_in",
            extensions={"opens_at": opens_at.isoformat()},
        )
    return _transition(
        reservation=reservation,
        by=by,
        new_status=ReservationStatus.CHECKED_IN,
        kind=ReservationEventKind.CHECKED_IN,
        audit_action=AuditAction.RESERVATION_CHECKED_IN,
        timestamp_field="checked_in_at",
        outbox_type="reservation.checked_in",
        allowed_from=(ReservationStatus.CONFIRMED,),
        request=request,
    )


def check_out(
    *, reservation: Reservation, by: User, request: HttpRequest | None = None
) -> Reservation:
    """تخلیه — پایان اقامت."""
    if not by.is_staff_member:
        raise PermissionDeniedError("تخلیه فقط توسط کارکنان انجام می‌شود.")
    if reservation.status != ReservationStatus.CHECKED_IN:
        raise ConflictError("تخلیه فقط برای رزرو پذیرش‌شده ممکن است.", code="not_checkout_able")
    return _transition(
        reservation=reservation,
        by=by,
        new_status=ReservationStatus.COMPLETED,
        kind=ReservationEventKind.CHECKED_OUT,
        audit_action=AuditAction.RESERVATION_CHECKED_OUT,
        timestamp_field="checked_out_at",
        outbox_type="reservation.checked_out",
        allowed_from=(ReservationStatus.CHECKED_IN,),
        request=request,
    )


def mark_no_show(
    *,
    reservation: Reservation,
    by: User,
    reason: str = "",
    request: HttpRequest | None = None,
) -> Reservation:
    """ثبت عدم‌حضور (سناریوی S5) — با پنجرهٔ مهلت سیاست."""
    if not by.is_staff_member:
        raise PermissionDeniedError("ثبت عدم‌حضور فقط توسط کارکنان انجام می‌شود.")
    if reservation.status != ReservationStatus.CONFIRMED:
        raise ConflictError("عدم‌حضور فقط برای رزرو تأییدشده ثبت می‌شود.", code="not_no_show_able")
    grace_hours = policy_int("reservation.no_show_grace_hours")
    if timezone.now() < reservation.starts_at + timedelta(hours=grace_hours):
        raise ConflictError(
            f"پنجرهٔ مهلت ورود ({grace_hours} ساعت) هنوز تمام نشده است.",
            code="grace_not_passed",
        )
    return _transition(
        reservation=reservation,
        by=by,
        new_status=ReservationStatus.NO_SHOW,
        kind=ReservationEventKind.NO_SHOW,
        audit_action=AuditAction.RESERVATION_NO_SHOW,
        timestamp_field="no_show_at",
        outbox_type="reservation.no_show",
        allowed_from=(ReservationStatus.CONFIRMED,),
        reason=reason,
        request=request,
    )


def _transition(
    *,
    reservation: Reservation,
    by: User,
    new_status: str,
    kind: str,
    audit_action: str,
    timestamp_field: str,
    outbox_type: str,
    allowed_from: tuple[str, ...] = (),
    reason: str = "",
    request: HttpRequest | None = None,
) -> Reservation:
    """موتور مشترک انتقال‌های کارمندی — یک مسیر، یک الگو، هیچ تکرار."""
    with transaction.atomic():
        locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
        # نگهبان وضعیت روی **ردیف قفل‌شده** بررسی می‌شود، نه نمونهٔ در حافظه؛
        # این هم شیء کهنه (stale) را می‌گیرد و هم مسابقهٔ هم‌زمانی را.
        if allowed_from and locked.status not in allowed_from:
            raise ConflictError(
                f"این انتقال از وضعیت «{locked.status_label()}» مجاز نیست.",
                code="invalid_transition",
            )
        previous = locked.status
        locked.status = new_status
        setattr(locked, timestamp_field, timezone.now())
        locked.save(update_fields=["status", timestamp_field, "updated_at"])
        locked.publish_event(
            kind, from_status=previous, to_status=new_status, actor=by, reason=reason
        )
        outbox.publish(
            outbox_type,
            aggregate_type="reservation",
            aggregate_id=locked.id,
            payload={
                "reservation_id": str(locked.id),
                "code": locked.code,
                "unit_code": locked.unit.code,
                "by": str(by.id),
            },
        )
        audit_record(
            AuditEntry(
                action=audit_action,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="reservation",
                target_id=str(locked.id),
                reason=reason,
                metadata={"code": locked.code, "to_status": new_status},
                request=request,
            )
        )
        invalidate_availability()
    metrics.RESERVATION_EVENTS.labels(event=new_status).inc()
    return locked


def expire_holds(*, now: datetime | None = None) -> int:
    """آزادسازی نگه‌داشت‌های منقضی (کار زمان‌بندی‌شده).

    سانس این رزروها **همین حالا** آزاد است، چون قید ``EXCLUDE`` تنها روی
    وضعیت‌های فعال اعمال می‌شود؛ این کار فقط وضعیت را صریح و قابل‌گزارش می‌کند.
    """
    moment = now or timezone.now()
    expired = list(selectors.expiring_holds(now=moment))
    if not expired:
        return 0
    released = 0
    freed: list[dict[str, Any]] = []
    with transaction.atomic():
        for reservation in expired:
            locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
            if locked.status != ReservationStatus.HOLD:
                continue
            locked.status = ReservationStatus.EXPIRED
            locked.save(update_fields=["status", "updated_at"])
            locked.publish_event(
                ReservationEventKind.EXPIRED,
                from_status=ReservationStatus.HOLD,
                to_status=ReservationStatus.EXPIRED,
                metadata={"expired_at": moment.isoformat()},
            )
            outbox.publish(
                "hold.expired",
                aggregate_type="reservation",
                aggregate_id=locked.id,
                payload={
                    "reservation_id": str(locked.id),
                    "unit_code": locked.unit.code,
                    "start": locked.starts_at.isoformat(),
                    "end": locked.ends_at.isoformat(),
                },
            )
            released += 1
            freed.append(
                {
                    "unit": locked.unit,
                    "start": locked.starts_at,
                    "end": locked.ends_at,
                    "reservation_id": locked.id,
                }
            )
        if released:
            invalidate_availability()
    for item in freed:
        _announce_freed_slot(
            unit=item["unit"],
            start=item["start"],
            end=item["end"],
            cause="hold_expired",
            reservation_id=item["reservation_id"],
        )
    logger.info("reservation_holds_expired", extra={"released": released})
    return released


def resolve_guest(*, phone: str, by: User) -> User:
    """یافتن یا ساخت حساب مهمان از شمارهٔ تلفن (مسیر تلفنی/پذیرش).

    حسابی که کارمند می‌سازد، «تلفن‌تأییدنشده» می‌ماند تا زائر در نخستین ورود
    با کد یک‌بارمصرف، مالکیت شماره را ثابت کند — نه کارمند به‌جای او.
    """
    from apps.identity.validators import normalize_phone

    normalized = normalize_phone(phone)
    user = User.objects.filter(phone=normalized).first()
    if user is not None:
        if user.deleted_at is not None:
            user.deleted_at = None
            user.save(update_fields=["deleted_at"])
        return user

    with transaction.atomic():
        created_user = User.objects.create_user(phone=normalized)
        outbox.publish(
            "identity.user_registered",
            aggregate_type="user",
            aggregate_id=created_user.id,
            payload={
                "user_id": str(created_user.id),
                "phone_masked": created_user.masked_phone,
                "created_by_staff": str(by.id),
            },
        )
        audit_record(
            AuditEntry(
                action=AuditAction.USER_REGISTERED,
                actor_id=by.id,
                actor_label=by.display_name,
                target_type="user",
                target_id=str(created_user.id),
                reason="ثبت رزرو تلفنی برای زائر بدون حساب",
                metadata={"channel": "staff", "phone_masked": created_user.masked_phone},
            )
        )
    return created_user


def staff_create_reservation(
    *,
    phone: str,
    unit: Unit,
    start: datetime,
    end: datetime,
    guests_count: int,
    by: User,
    source: str = ReservationSource.PHONE,
    first_name: str = "",
    last_name: str = "",
    province: str = "",
    city: str = "",
    guest_note: str = "",
    staff_note: str = "",
    request: HttpRequest | None = None,
) -> Reservation:
    """ثبت رزرو از سوی کارکنان برای یک زائر (تلفنی یا حضوری).

    اگر زائر پروفایل کامل نداشته باشد و کارمند مشخصات را بدهد، همان‌جا ثبت
    می‌شود (بدون بند کد ملی — استثنای مستند مسیر پذیرش).
    """
    if not by.is_staff_member:
        raise PermissionDeniedError("ثبت رزرو تلفنی فقط برای کارکنان مجاز است.")
    guest = resolve_guest(phone=phone, by=by)
    if not guest.is_profile_complete and first_name and last_name and province and city:
        guest.complete_profile(
            first_name=first_name, last_name=last_name, province=province, city=city
        )

    cfg = lattice_config()
    violations = collect_violations(
        guest=guest,
        unit=unit,
        start=start,
        end=end,
        guests_count=guests_count,
        config=cfg,
        require_profile=False,
    )
    if violations:
        raise PolicyViolationError(violations[0]["message"], extensions={"violations": violations})

    return create_reservation(
        guest=guest,
        unit=unit,
        start=start,
        end=end,
        guests_count=guests_count,
        require_profile=False,
        source=source,
        actor=by,
        guest_note=guest_note,
        staff_note=staff_note,
        request=request,
    )


def update_staff_note(
    *, reservation: Reservation, by: User, note: str, request: HttpRequest | None = None
) -> Reservation:
    """ثبت یادداشت داخلی کارکنان روی رزرو."""
    if not by.is_staff_member:
        raise PermissionDeniedError("ثبت یادداشت داخلی فقط برای کارکنان مجاز است.")
    reservation.staff_note = note[:300]
    reservation.save(update_fields=["staff_note", "updated_at"])
    reservation.publish_event(
        ReservationEventKind.NOTE, actor=by, reason=note[:300], metadata={"field": "staff_note"}
    )
    audit_record(
        AuditEntry(
            action=AuditAction.RESERVATION_CREATED,
            actor_id=by.id,
            actor_label=by.display_name,
            target_type="reservation",
            target_id=str(reservation.id),
            metadata={"operation": "staff_note"},
            request=request,
        )
    )
    return reservation
