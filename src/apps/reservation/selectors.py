"""پرس‌وجوهای خواندنی رزرو — مبنای سهمیه‌ها، تقویم و تابلوی کارکنان."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from django.db.models import Count, Max, Q, QuerySet, Sum
from django.utils import timezone
from psycopg.types.range import Range

from apps.core.text import to_latin_digits
from apps.identity.choices import UserRole
from apps.identity.models import User
from apps.inventory.models import Unit
from apps.inventory.selectors import day_window
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation, ReservationEvent

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


@dataclass(frozen=True, slots=True)
class BoardFilter:
    """فیلترهای تابلوی کارکنان — همه اختیاری؛ خالی یعنی «همه».

    ``query`` هوشمند است: کد رزرو («M-1405-0012» یا فقط «12»)، بخشی از شماره
    (با رقم فارسی یا لاتین، با ۰ یا ۹۸+) یا بخشی از نام/نام خانوادگی.
    """

    query: str = ""
    statuses: tuple[str, ...] = ()
    unit_codes: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


PHONE_QUERY_MIN_DIGITS = 4
_NATIONAL_MOBILE_DIGITS = 10


def phone_fragment(digits: str) -> str:
    """بخش جست‌وجوپذیر شماره — پیشوندهای کشور/صفرِ ملی حذف، بقیه دست‌نخورده.

    فقط پیشوندِ «کامل» برداشته می‌شود: «۰۰۹۸»/«۹۸» وقتی پس از آن شمارهٔ ده‌رقمی
    یا بخشی که با ۹ شروع می‌شود بیاید، و «۰» تنها وقتی شماره با «۰۹» شروع شود.
    تکه‌هایی مثل «۰۰۰۱» دست‌نخورده می‌مانند (نه «۱» که با همه‌چیز جور می‌شود).
    """
    fragment = digits.removeprefix("00")
    if fragment.startswith("98") and len(fragment) > _NATIONAL_MOBILE_DIGITS:
        return fragment[2:]
    if fragment.startswith("989"):
        return fragment[2:]
    if fragment.startswith("09"):
        return fragment[1:]
    return digits


def _query_filter(raw: str) -> Q:
    """ترجمهٔ عبارت جست‌وجوی پذیرش به شرط ORM (کد ∪ تلفن ∪ نام)."""
    text = to_latin_digits(raw).strip()
    condition = Q(code__icontains=text)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= PHONE_QUERY_MIN_DIGITS and len(digits) >= len(text.replace(" ", "")) - 1:
        # «0912…» و «98912…» و «+98912…» همه به رقم‌های ملی بدون پیشوند بدل می‌شوند.
        condition |= Q(guest__phone__contains=phone_fragment(digits))
    for token in text.split():
        condition |= Q(guest__first_name__icontains=token) | Q(guest__last_name__icontains=token)
    return condition


def staff_board(
    *, start: datetime, end: datetime, filters: BoardFilter | None = None
) -> QuerySet[Reservation]:
    """تابلوی کارکنان — همهٔ رزروهای بازه، به‌ترتیب زمان (شامل لغوشده‌ها)."""
    queryset = _base().filter(stay_period__overlap=Range(start, end))
    if filters is not None:
        if filters.query.strip():
            queryset = queryset.filter(_query_filter(filters.query))
        if filters.statuses:
            queryset = queryset.filter(status__in=filters.statuses)
        if filters.unit_codes:
            queryset = queryset.filter(unit__code__in=filters.unit_codes)
        if filters.sources:
            queryset = queryset.filter(source__in=filters.sources)
    return queryset.order_by("stay_period")


def events_for(reservation: Reservation) -> QuerySet[ReservationEvent]:
    """رخدادهای یک رزرو به ترتیب زمان (تاریخچهٔ کامل زندگی رزرو)."""
    return reservation.events.select_related("actor").order_by("created_at")


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


def guest_directory(query: str = "") -> QuerySet[User]:
    """فهرست زائران برای میز پذیرش — با شمارش رزرو، عدم‌حضور و آخرین اقامت.

    جست‌وجو همان منطق تابلوی پذیرش را دارد: بخشی از شماره یا نام. کارکنان
    خودشان در این فهرست نمی‌آیند (فهرست «زائران» است، نه «کاربران»).
    """
    queryset = User.objects.filter(deleted_at__isnull=True, is_staff=False).exclude(
        role__in=UserRole.staff_roles()
    )
    text = to_latin_digits(query).strip()
    if text:
        condition = Q(first_name__icontains=text) | Q(last_name__icontains=text)
        for token in text.split():
            condition |= Q(first_name__icontains=token) | Q(last_name__icontains=token)
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= PHONE_QUERY_MIN_DIGITS:
            condition |= Q(phone__contains=phone_fragment(digits))
        queryset = queryset.filter(condition)
    return queryset.annotate(
        reservations_count=Count("reservations", distinct=True),
        no_show_count=Count(
            "reservations",
            filter=Q(reservations__status=ReservationStatus.NO_SHOW),
            distinct=True,
        ),
        last_stay_end=Max("reservations__stay_period__endswith"),
    ).order_by("-date_joined", "-id")


def guest_by_id(guest_id: UUID) -> User | None:
    """یک زائر (نه کارمند) با شناسه — برای کارت زائر در پنل پذیرش."""
    return guest_directory().filter(pk=guest_id).first()
