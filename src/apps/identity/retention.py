"""نگهداری دادهٔ زائر — کمینه‌سازی پس از پایان پنجرهٔ نگهداری.

اصل حاکم: «هر داده‌ای که برای ارائهٔ خدمت لازم نیست، نگه‌داشتنش فقط ریسک است.»
زائرسرا برای اقامت، نام و کد ملی می‌گیرد و — بعد از چند سال — دیگر برای عملیات
لازمش ندارد. اما **حذف کامل حساب** هم درست نیست: تاریخ اقامت‌ها مبنای آمار و
جلوگیری از سوءاستفاده است. پس مسیر ما «کمینه‌سازی» است:

    • نام، نام خانوادگی، شهر، استان و کد ملی (رمزنگاری‌شده و ایندکس کور) پاک می‌شوند.
    • شمارهٔ تلفن **می‌ماند**: شناسهٔ ورود است و بدون آن، زائر قدیمی نمی‌تواند
      برگردد. (حذف کامل، روشی جدا و با درخواست خودِ زائر است — در رانبوک.)
    • رزروها **دست‌نخورده** می‌مانند: آمار ظرفیت نباید با پاک‌کردن هویت بشکند.
    • هر پاک‌سازی در زنجیرهٔ حسابرسی ثبت می‌شود (چه کسی، چه وقت، چرا).

پنجره از سیاست ``data.guest_retention_days`` می‌آید (پیش‌فرض ۱۰۹۵ روز = سه سال):
مدیر می‌تواند بدون انتشار نسخهٔ تازه آن را تغییر دهد.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone
from psycopg.types.range import Range

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.identity.choices import UserRole
from apps.identity.models import User
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import policy_int

RETENTION_POLICY_KEY = "data.guest_retention_days"
DEFAULT_RETENTION_DAYS = 1095
DEFAULT_LIMIT = 200
REDACTION_REASON = "کمینه‌سازی خودکار پس از پایان پنجرهٔ نگهداری"
REDACTED_FIRST_NAME = "زائر"


@dataclass(slots=True)
class RetentionRun:
    """نتیجهٔ یک اجرای نگهداری — برای گزارش دستور و آزمون‌ها."""

    window_days: int
    dry_run: bool
    scanned: int = 0
    redacted: int = 0
    skipped_active: int = 0
    skipped_recent: int = 0
    codes: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        """گزارش فارسی یک‌خطی."""
        mode = "آزمایشی" if self.dry_run else "اجرا"
        return (
            f"{mode}: بررسی‌شده {self.scanned} · کمینه‌شده {self.redacted} · "
            f"دارای رزرو فعال {self.skipped_active} · در پنجرهٔ نگهداری {self.skipped_recent}"
        )


def retention_window_days() -> int:
    """پنجرهٔ نگهداری (روز) از سیاست — با کف ۳۰ روز تا خطای پیکربندی فاجعه نسازد."""
    return max(30, policy_int(RETENTION_POLICY_KEY))


def redaction_cutoff(*, now: datetime | None = None, window_days: int | None = None) -> datetime:
    """مرز زمانی: پیش از این لحظه، دادهٔ شناسایی‌کننده کمینه می‌شود."""
    moment = now or timezone.now()
    return moment - timedelta(
        days=window_days if window_days is not None else retention_window_days()
    )


def active_reservation_exists(*, now: datetime | None = None) -> Exists:
    """شرط SQL: زائر رزرو فعال دارد (اقامت جاری یا آینده).

    نکتهٔ مهندسی: ``Max("reservations__stay_period__upper")`` روی فیلد بازه‌ای
    **کار نمی‌کند** (ORM روی بازه، تراِنزفورم ``upper`` ندارد). پس بازهٔ فعال را با
    ``overlap`` می‌سنجیم — همان چیزی که قید دیتابیس و موتور دسترس‌پذیری هم استفاده
    می‌کند؛ یعنی یک تعریف، نه دو تعریف.
    """
    moment = now or timezone.now()
    return Exists(
        Reservation.objects.filter(
            guest=OuterRef("pk"),
            status__in=ReservationStatus.active_statuses(),
            stay_period__overlap=Range(moment, None),
        )
    )


def latest_stay_end(guest: User) -> datetime | None:
    """پایانِ آخرین اقامت زائر (یا تهی) — یک کوئری کوچک روی کلید فهرست‌شده."""
    period = (
        Reservation.objects.filter(guest=guest)
        .order_by("-stay_period")
        .values_list("stay_period", flat=True)
        .first()
    )
    return period.upper if period else None


@dataclass(slots=True)
class CandidateScan:
    """نتیجهٔ غربال: واجدین شرط + دلیل کنارگذاشته‌شده‌ها (برای گزارش شفاف)."""

    eligible: list[User]
    skipped_active: int = 0
    skipped_recent: int = 0


def candidates(*, now: datetime | None = None, window_days: int | None = None) -> CandidateScan:
    """غربال زائران برای کمینه‌سازی — دو مرحله: SQL، سپس بازهٔ آخرین اقامت.

    شرط‌ها: نقش زائر · فعال · کمینه‌نشده · رزرو فعال ندارد · آخرین ورود/ثبت و
    پایان آخرین اقامت، پیش از مرز نگهداری است.
    """
    moment = now or timezone.now()
    cutoff = redaction_cutoff(now=moment, window_days=window_days)
    alive = User.objects.filter(role=UserRole.GUEST, is_active=True, redacted_at__isnull=True)
    active = active_reservation_exists(now=moment)

    scan = CandidateScan(eligible=[], skipped_active=alive.filter(active).count())
    narrowed = (
        alive.exclude(active)
        .filter(Q(last_login__lt=cutoff) | Q(last_login__isnull=True))
        .filter(Q(date_joined__lt=cutoff))
        .annotate(last_booking=Max("reservations__created_at"))
        .filter(Q(last_booking__lt=cutoff) | Q(last_booking__isnull=True))
        .order_by("date_joined")
    )
    for guest in narrowed:
        stay_end = latest_stay_end(guest)
        if stay_end is not None and stay_end >= cutoff:
            scan.skipped_recent += 1
            continue
        scan.eligible.append(guest)
    return scan


def redact_guest(
    guest: User,
    *,
    reason: str = REDACTION_REASON,
    actor_label: str = "retention_sweep",
) -> User:
    """کمینه‌سازی هویت یک زائر — اتمیک و ثبت‌شده در زنجیرهٔ حسابرسی.

    بار دوم روی همان زائر، بی‌اثر است (idempotent): مهر ``redacted_at`` دوباره
    نوشته نمی‌شود و رکورد حسابرسی تکراری ساخته نمی‌شود.
    """
    if guest.redacted_at is not None:
        return guest

    with transaction.atomic():
        guest = User.objects.select_for_update().get(pk=guest.pk)
        if guest.redacted_at is not None:  # pragma: no cover - رقابت دو اجرای هم‌زمان
            return guest
        guest.first_name = REDACTED_FIRST_NAME
        guest.last_name = ""
        guest.city = ""
        guest.province = ""
        guest.email = ""
        guest.national_id_encrypted = ""
        guest.national_id_blind = ""
        guest.last_login_ip = None
        guest.redacted_at = timezone.now()
        guest.save(
            update_fields=[
                "first_name",
                "last_name",
                "city",
                "province",
                "email",
                "national_id_encrypted",
                "national_id_blind",
                "last_login_ip",
                "redacted_at",
            ]
        )

    audit_record(
        AuditEntry(
            action=AuditAction.GUEST_REDACTED,
            actor_label=actor_label,
            target_type="user",
            target_id=str(guest.pk),
            reason=reason,
            metadata={"window": RETENTION_POLICY_KEY},
        )
    )
    return guest


def run_guest_retention(
    *,
    dry_run: bool = False,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> RetentionRun:
    """اجرای نگهداری روی دستهٔ زائران واجد شرط (با گزارش شفاف).

    ``dry_run`` هیچ نوشتنی انجام نمی‌دهد — همان چیزی که پیش از نخستین اجرای واقعی
    روی دادهی تولیدی لازم است.
    """
    window_days = retention_window_days()
    run = RetentionRun(window_days=window_days, dry_run=dry_run)
    scan = candidates(now=now, window_days=window_days)
    run.scanned = len(scan.eligible)
    run.skipped_active = scan.skipped_active
    run.skipped_recent = scan.skipped_recent
    selected = scan.eligible[: max(1, limit)]
    if dry_run:
        run.codes = tuple(str(item.pk) for item in selected)
        return run

    for guest in selected:
        redact_guest(guest)
        run.redacted += 1
    return run


def erase_guest_data(guest: User, *, reason: str, actor_label: str) -> User:
    """کمینه‌سازی به‌درخواست خودِ زائر (حق پاک‌کردن) — بدون انتظار پنجره.

    تفاوت با مسیر خودکار فقط در «چرا» است: این‌جا دلیل اجباری است، چون کنش با
    درخواست انسانی رخ می‌دهد و باید قابل حسابرسی باشد.
    """
    if not reason.strip():
        msg = "برای پاک‌سازی به‌درخواست، ثبت دلیل اجباری است."
        raise ValueError(msg)
    return redact_guest(guest, reason=reason, actor_label=actor_label)


def retention_stats(
    *, now: datetime | None = None, window_days: int | None = None
) -> dict[str, Any]:
    """آمار سریع برای گزارش عملیاتی (بدون نوشتن)."""
    window = window_days if window_days is not None else retention_window_days()
    cutoff = redaction_cutoff(now=now, window_days=window)
    scan = candidates(now=now, window_days=window)
    eligible = len(scan.eligible)
    already = User.objects.filter(redacted_at__isnull=False).count()
    pending_recent = User.objects.filter(
        role=UserRole.GUEST, redacted_at__isnull=True, date_joined__gte=cutoff
    ).count()
    return {
        "window_days": window,
        "cutoff": cutoff.isoformat(),
        "eligible": eligible,
        "already_redacted": already,
        "inside_window": pending_recent,
    }
