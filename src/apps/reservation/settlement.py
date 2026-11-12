"""چرخهٔ پایان اقامت — «فاز» زنده‌ی هر رزرو و بستن خودکار پرونده‌های رهاشده.

مسئله: پروندهٔ رزرو فقط با کنش پیشخوان (تخلیه یا عدم‌حضور) بسته می‌شد. اگر خادم تخلیه را
ثبت نمی‌کرد یا زائرِ تلفنی بی‌پذیرش می‌آمد و می‌رفت، رزرو برای همیشه «تأییدشده» می‌ماند:
سقف رزرو فعال پر می‌ماند، دفتر دل‌نوشته باز نمی‌شد و آمار اشغال خطا می‌کرد. سند دامنه
(بخش ۵.۱) از روز نخست انتقال «پایان‌یافته» را به «کارمند/زمان‌بند» سپرده بود؛ این ماژول
نیمهٔ «زمان‌بند» آن است.

دو لایهٔ مکمّل:
    ۱. **سنجهٔ زمان (فوری):** ``stay_phase`` و ``selectors.active_count`` پایان اقامت را از
       ساعت می‌خوانند، نه از وضعیت؛ پس حتی اگر کرون عقب بیفتد، زائر بی‌دلیل مسدود نمی‌شود.
    ۲. **بستن پرونده (پایدار):** ``settle_ended_stays`` پس از مهلت سیاستی
       (``reservation.auto_settle_after_hours``) وضعیت را صریح، رخدادنگاری و حسابرسی می‌کند
       تا نظرسنجی، گزارش و تابلوی پیشخوان همگی یک حقیقت را ببینند.

حالت‌های بستن (``mode`` در فرادادهٔ رخداد و حسابرسی):
    • ``checked_out`` — پذیرش‌شده ⇒ «پایان‌یافته»؛ زمان تخلیه = پایان اقامت.
    • ``unattended`` — تأییدشدهٔ بی‌پذیرش ⇒ «پایان‌یافته» بی ``checked_in_at`` (پیش‌فرض).
    • ``no_show`` — همان، ولی اگر سیاست «عدم‌حضور خودکار» روشن باشد ⇒ «عدم‌حضور» با محرومیت.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import metrics, outbox
from apps.reservation import selectors
from apps.reservation.availability import invalidate_availability
from apps.reservation.choices import ReservationEventKind, ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import policy_bool, policy_int

logger = logging.getLogger(__name__)

# ── فاز زندهٔ اقامت ────────────────────────────────────────────────────────
UPCOMING: Final[str] = "upcoming"
ONGOING: Final[str] = "ongoing"
SETTLING: Final[str] = "settling"
CLOSED: Final[str] = "closed"

PHASE_LABELS: Final[dict[str, str]] = {
    UPCOMING: "پیش رو",
    ONGOING: "در جریان اقامت",
    SETTLING: "پایان اقامت · در حال بستن پرونده",
    CLOSED: "بسته‌شده",
}
PHASES: Final[tuple[str, ...]] = tuple(PHASE_LABELS)

# ── بستن خودکار ────────────────────────────────────────────────────────────
MODE_CHECKED_OUT: Final[str] = "checked_out"
MODE_UNATTENDED: Final[str] = "unattended"
MODE_NO_SHOW: Final[str] = "no_show"
SYSTEM_ACTOR: Final[str] = "سامانه — بستن خودکار پرونده"
DEFAULT_LIMIT: Final[int] = 200

REASONS: Final[dict[str, str]] = {
    MODE_CHECKED_OUT: "اقامت به پایان رسید و تخلیه در پیشخوان ثبت نشده بود؛ پرونده خودکار بسته شد.",
    MODE_UNATTENDED: (
        "پذیرش حضوری برای این رزرو ثبت نشده بود؛ پرونده پس از پایان اقامت خودکار بسته شد."
    ),
    MODE_NO_SHOW: "تا پایان اقامت پذیرشی ثبت نشد؛ طبق سیاست «عدم‌حضور خودکار» بسته شد.",
}

_OPEN: Final[tuple[str, ...]] = (ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN)


def settle_after() -> timedelta:
    """مهلت پس از پایان اقامت تا بستن خودکار (از سیاست؛ هرگز منفی نیست)."""
    return timedelta(hours=max(0, policy_int("reservation.auto_settle_after_hours")))


def stay_phase(reservation: Reservation, *, now: datetime | None = None) -> str:
    """فاز اقامت در همین لحظه — منبع واحد حقیقت برای پرتال، رسید و پیشخوان."""
    moment = now or timezone.now()
    if reservation.status not in ReservationStatus.active_statuses():
        return CLOSED
    if moment >= reservation.ends_at:
        return SETTLING
    if moment >= reservation.starts_at or reservation.status == ReservationStatus.CHECKED_IN:
        return ONGOING
    return UPCOMING


def settles_at(reservation: Reservation, *, after: timedelta | None = None) -> datetime | None:
    """لحظهٔ بستن خودکار پروندهٔ باز (یا ``None`` برای پروندهٔ بسته/نگه‌داشت)."""
    if reservation.status not in _OPEN:
        return None
    return reservation.ends_at + (after if after is not None else settle_after())


@dataclass(frozen=True, slots=True)
class SettlementReport:
    """کارنامهٔ یک نوبت بستن خودکار."""

    checked_out: int = 0
    unattended: int = 0
    no_show: int = 0
    examined: int = 0

    @property
    def settled(self) -> int:
        """همهٔ پرونده‌های بسته‌شده در این نوبت."""
        return self.checked_out + self.unattended + self.no_show

    def summary(self) -> str:
        """خلاصهٔ فارسی برای لاگ کرون و خروجی فرمان."""
        return (
            f"بستن خودکار: {self.settled} پرونده "
            f"(تخلیهٔ ثبت‌نشده {self.checked_out}، بی‌پذیرش {self.unattended}، "
            f"عدم‌حضور {self.no_show}) از {self.examined} بررسی‌شده"
        )


def _settle_one(pk: UUID, *, moment: datetime, cutoff: datetime, as_no_show: bool) -> str | None:
    """بستن یک پرونده در تراکنش خودش — شکست یکی، بقیه را برنمی‌گرداند."""
    with transaction.atomic():
        locked = (
            Reservation.objects.select_for_update().select_related("unit").filter(pk=pk).first()
        )
        # نگهبان روی ردیف قفل‌شده: پیشخوان ممکن است همین لحظه تخلیه یا لغو را ثبت کرده باشد.
        if locked is None or locked.status not in _OPEN or locked.ends_at > cutoff:
            return None
        previous = locked.status
        fields = ["status", "updated_at"]
        if previous == ReservationStatus.CHECKED_IN:
            mode, new_status = MODE_CHECKED_OUT, ReservationStatus.COMPLETED
            # لحظهٔ واقعی خروج نامعلوم است؛ پایان اقامت صادقانه‌ترین برآورد است (نه لحظهٔ اجرای کرون).
            locked.checked_out_at = locked.ends_at
            fields.append("checked_out_at")
        elif as_no_show:
            mode, new_status = MODE_NO_SHOW, ReservationStatus.NO_SHOW
            locked.no_show_at = moment
            fields.append("no_show_at")
        else:
            mode, new_status = MODE_UNATTENDED, ReservationStatus.COMPLETED
        locked.status = new_status
        locked.save(update_fields=fields)
        reason = REASONS[mode]
        locked.publish_event(
            ReservationEventKind.NO_SHOW if mode == MODE_NO_SHOW else ReservationEventKind.SETTLED,
            from_status=previous,
            to_status=new_status,
            reason=reason,
            metadata={"mode": mode, "automatic": True, "settled_at": moment.isoformat()},
        )
        outbox.publish(
            "reservation.settled",
            aggregate_type="reservation",
            aggregate_id=locked.id,
            payload={
                "reservation_id": str(locked.id),
                "code": locked.code,
                "unit_code": locked.unit.code,
                "mode": mode,
                "to_status": new_status,
            },
        )
        audit_record(
            AuditEntry(
                action=(
                    AuditAction.RESERVATION_NO_SHOW
                    if mode == MODE_NO_SHOW
                    else AuditAction.RESERVATION_SETTLED
                ),
                actor_label=SYSTEM_ACTOR,
                target_type="reservation",
                target_id=str(locked.id),
                reason=reason,
                metadata={
                    "code": locked.code,
                    "mode": mode,
                    "from_status": previous,
                    "to_status": new_status,
                },
            )
        )
    metrics.RESERVATION_EVENTS.labels(event=new_status).inc()
    return mode


def settle_ended_stays(
    *, now: datetime | None = None, limit: int = DEFAULT_LIMIT, dry_run: bool = False
) -> SettlementReport:
    """بستن پرونده‌های اقامتِ تمام‌شده‌ای که پیشخوان پس از مهلت سیاستی نبسته است.

    هم‌توان (idempotent) است: اجرای دوباره چیزی را دوباره نمی‌بندد. ``dry_run`` فقط می‌شمارد
    که هر پرونده با کدام حالت بسته **خواهد شد** — برای بازبینی پیش از نخستین اجرا در تولید.
    """
    moment = now or timezone.now()
    cutoff = moment - settle_after()
    as_no_show = policy_bool("reservation.auto_no_show_unattended")
    candidates = selectors.settlement_candidates(before=cutoff, limit=max(1, limit))
    counts = {MODE_CHECKED_OUT: 0, MODE_UNATTENDED: 0, MODE_NO_SHOW: 0}
    for reservation in candidates:
        if dry_run:
            if reservation.status == ReservationStatus.CHECKED_IN:
                mode: str | None = MODE_CHECKED_OUT
            else:
                mode = MODE_NO_SHOW if as_no_show else MODE_UNATTENDED
        else:
            mode = _settle_one(reservation.pk, moment=moment, cutoff=cutoff, as_no_show=as_no_show)
        if mode is not None:
            counts[mode] += 1
    report = SettlementReport(
        checked_out=counts[MODE_CHECKED_OUT],
        unattended=counts[MODE_UNATTENDED],
        no_show=counts[MODE_NO_SHOW],
        examined=len(candidates),
    )
    if report.settled and not dry_run:
        invalidate_availability()
    logger.info(
        "reservation_stays_settled",
        extra={
            "settled": report.settled,
            "checked_out": report.checked_out,
            "unattended": report.unattended,
            "no_show": report.no_show,
            "dry_run": dry_run,
        },
    )
    return report
