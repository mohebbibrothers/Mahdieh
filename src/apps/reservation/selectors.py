"""پرس‌وجوهای خواندنی رزرو — مبنای سهمیه‌ها، تقویم و تابلوی کارکنان."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from django.db.models import QuerySet, Sum
from django.utils import timezone
from psycopg.types.range import Range

from apps.identity.models import User
from apps.inventory.models import Unit
from apps.inventory.selectors import day_window
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation

UPCOMING = "upcoming"
PAST = "past"
ALL = "all"


def _base() -> QuerySet[Reservation]:
    """کوئری پایه با پیش‌بارگذاری واحد، مهمان و طبقه (بدون N+1)."""
    return Reservation.objects.select_related("unit", "unit__floor", "guest", "created_by")


def reservations_for_user(user: User, *, scope: str = UPCOMING) -> QuerySet[Reservation]:
    """رزروهای یک زائر — آینده، گذشته یا همه."""
    queryset = _base().filter(guest=user)
    now = timezone.now()
    if scope == UPCOMING:
        # «جاری/آینده» = اقامتی که هنوز به پایان نرسیده است.
        # نکتهٔ فنی: tstzrange را نمی‌توان با یک لحظه مقایسه کرد؛ از ترنسفورم
        # ``endswith`` (معادل upper(range) در Postgres) استفاده می‌کنیم.
        queryset = queryset.filter(stay_period__endswith__gt=now)
    elif scope == PAST:
        queryset = queryset.filter(stay_period__endswith__lt=now)
    return queryset.order_by("-stay_period")


def reservation_by_code(code: str) -> Reservation | None:
    """یافتن رزرو با کد انسان‌خوان (بدون حساسیت به بزرگی/کوچکی حرف)."""
    return _base().filter(code__iexact=code.strip()).first()


def active_conflicts(
    *, unit: Unit, start: datetime, end: datetime, exclude_pk: UUID | None = None
) -> QuerySet[Reservation]:
    """رزروهای فعالی که با بازهٔ مسدودکنندهٔ درخواستی هم‌پوشانی دارند."""
    queryset = Reservation.objects.filter(
        unit=unit,
        status__in=ReservationStatus.active_statuses(),
        block_period__overlap=Range(start, end),
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset


def cells_used(
    *, guest: User, start: datetime, end: datetime, exclude_pk: UUID | None = None
) -> int:
    """مجموع نیم‌روزهای مصرف‌شدهٔ کاربر در یک بازهٔ چرخشی (سهمیهٔ عادلانه)."""
    queryset = Reservation.objects.filter(
        guest=guest,
        status__in=ReservationStatus.active_statuses(),
        created_at__gte=start,
        created_at__lt=end,
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    total = queryset.aggregate(total=Sum("cells_count"))["total"]
    return int(total or 0)


def active_count(guest: User, *, exclude_pk: UUID | None = None) -> int:
    """تعداد رزروهای فعال جاری کاربر."""
    queryset = Reservation.objects.filter(
        guest=guest, status__in=ReservationStatus.active_statuses()
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.count()


def last_reservation_end(
    guest: User, *, before: datetime | None = None, exclude_pk: UUID | None = None
) -> datetime | None:
    """پایان آخرین رزروی که زائر **واقعاً گرفته است** (مبنای قاعدهٔ فاصله).

    نکته‌های ظریف که باگ‌های آینده را می‌بندند:
        • رزروهای لغو‌شده/منقضی/جانشین‌شده به‌حساب نمی‌آیند (``held_statuses``).
        • اگر ``before`` داده شود، تنها رزروهایی دیده می‌شوند که **پیش از** آن
          لحظه تمام شده‌اند؛ وگرنه درخواست بازهٔ قدیمی‌تر بی‌دلیل رد می‌شد.
    """
    queryset = Reservation.objects.filter(guest=guest, status__in=ReservationStatus.held_statuses())
    if before is not None:
        queryset = queryset.filter(stay_period__endswith__lte=before)
    queryset = queryset.order_by("-stay_period")
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    reservation = queryset.first()
    return reservation.ends_at if reservation else None


def pending_cooldown_until(guest: User, *, hours: int) -> datetime | None:
    """پایان خنک‌شدن پس از لغو دیرهنگام (اگر فعال باشد)."""
    latest = (
        Reservation.objects.filter(guest=guest, is_late_cancel=True, cancelled_at__isnull=False)
        .order_by("-cancelled_at")
        .first()
    )
    if latest is None or latest.cancelled_at is None:
        return None
    until = latest.cancelled_at + timedelta(hours=hours)
    return until if until > timezone.now() else None


def suspended_until(guest: User, *, days: int) -> datetime | None:
    """پایان محرومیت پس از عدم‌حضور (اگر فعال باشد)."""
    latest = (
        Reservation.objects.filter(guest=guest, no_show_at__isnull=False)
        .order_by("-no_show_at")
        .first()
    )
    if latest is None or latest.no_show_at is None:
        return None
    until = latest.no_show_at + timedelta(days=days)
    return until if until > timezone.now() else None


def reservations_overlapping(*, start: datetime, end: datetime) -> QuerySet[Reservation]:
    """همهٔ رزروهای فعال هم‌پوشان با یک بازه (برای محاسبهٔ تقویم)."""
    return _base().filter(
        status__in=ReservationStatus.active_statuses(),
        stay_period__overlap=Range(start, end),
    )


def staff_board(*, start: datetime, end: datetime) -> QuerySet[Reservation]:
    """تابلوی کارکنان — همهٔ رزروهای بازه، به‌ترتیب زمان (شامل لغوشده‌ها)."""
    return _base().filter(stay_period__overlap=Range(start, end)).order_by("stay_period")


def arrivals_for_day(day: date) -> QuerySet[Reservation]:
    """ورودی‌های یک روز — برای برگهٔ روز پذیرش."""
    start, end = day_window(day)
    return (
        _base()
        .filter(status=ReservationStatus.CONFIRMED, stay_period__overlap=Range(start, end))
        .order_by("stay_period")
    )


def expiring_holds(*, now: datetime | None = None) -> QuerySet[Reservation]:
    """نگه‌داشت‌های منقضی‌شده که باید آزاد شوند."""
    moment = now or timezone.now()
    return Reservation.objects.filter(
        status=ReservationStatus.HOLD, hold_expires_at__lte=moment
    ).select_related("unit", "guest")
