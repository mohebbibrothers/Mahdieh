"""دستور ``seed_demo`` — دادهٔ نمایشی واقع‌گرا برای توسعه، دمو و آزمون دستی.

چرا از «سرویس‌ها» استفاده می‌کند؟ چون دادهٔ نمایشی هم باید از همان مسیری برود که
کاربر واقعی می‌رود: رخداد، Outbox، حسابرسی و پیامک. اگر سرویس خراب باشد، این
دستور هم خراب می‌شود — که خودش یک آزمون یکپارچهٔ رایگان است.

دستور **idempotent** است: اجرای دوباره دادهٔ تکراری نمی‌سازد، چون قواعد واقعی
سامانه (یک رزرو فعال برای هر زائر، فاصلهٔ بین رزروها) این‌جا هم حاکم‌اند.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.identity.choices import Province, UserRole
from apps.identity.models import User
from apps.inventory.lattice import cell_at_boundary, cells_for_day, period_from_index
from apps.inventory.models import Block, Unit
from apps.inventory.services import create_block
from apps.reservation import selectors
from apps.reservation.choices import ReservationSource
from apps.reservation.models import Reservation
from apps.reservation.services import (
    cancel_reservation,
    check_in,
    create_reservation,
    staff_create_reservation,
)

DEMO_GUESTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("09120001001", "محمد", "رضایی", Province.KHORASAN_RAZAVI, "مشهد"),
    ("09120001002", "فاطمه", "حسینی", Province.TEHRAN, "تهران"),
    ("09120001003", "زهرا", "کریمی", Province.ISFAHAN, "اصفهان"),
    ("09120001004", "علی", "موسوی", Province.FARS, "شیراز"),
    ("09120001005", "مریم", "صادقی", Province.KHORASAN_NORTH, "بجنورد"),
    ("09120001006", "حسین", "جعفری", Province.QOM, "قم"),
    ("09120001007", "نرجس", "بهرامی", Province.YAZD, "یزد"),
    ("09120001008", "امیر", "شریفی", Province.GILAN, "رشت"),
    ("09120001009", "سکینه", "مرادی", Province.KERMAN, "کرمان"),
    ("09120001010", "رضا", "اسدی", Province.MAZANDARAN, "ساری"),
)

NATIONAL_ID_SEED_PREFIX = "1405"
BASE_CELLS = 2
CELLS_VARIETY = 3
MAX_GUESTS_PER_RESERVATION = 4
CANCELLED_INDEX = 1
CHECKED_IN_INDEX = 3
PHONE_BOOKING_OFFSET_DAYS = 12
BLOCK_OFFSET_DAYS = 20
DEMO_NOTE = "ورود با اتوبوس؛ درب ورودی بسته است."
BLOCK_REASON = "سرویس دوره‌ای سیستم گرمایش"
DEFAULT_EXPECTED_ARRIVAL = time(16, 0)
CHECK_DIGIT_PIVOT = 2


class Command(BaseCommand):
    """ساخت زائران نمونه و چند رزرو واقعی روی تقویم نیم‌روزی."""

    help = "ساخت دادهٔ نمایشی (زائر، رزرو، لغو، پذیرش و بلوک نگهداری)"

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌های تعداد."""
        parser.add_argument("--guests", type=int, default=8, help="تعداد زائر نمونه")
        parser.add_argument("--reservations", type=int, default=8, help="تعداد رزرو نمونه")

    def handle(self, *_args: object, **options: object) -> None:
        """ساخت دادهٔ نمونه به‌صورت idempotent."""
        guests = self._ensure_guests(int(options["guests"]))  # type: ignore[call-overload]
        units = list(Unit.objects.filter(is_active=True).order_by("sort_order", "code"))
        if not units:
            self.stderr.write(self.style.ERROR("ابتدا «seed_inventory» را اجرا کنید."))
            return

        created = self._ensure_reservations(
            guests,
            units,
            int(options["reservations"]),  # type: ignore[call-overload]
        )
        self._ensure_phone_reservation(units)
        self._ensure_block(units)
        self.stdout.write(
            self.style.SUCCESS(
                f"زائران: {len(guests)} · رزروهای تازه: {created} · واحدها: {len(units)}"
            )
        )

    # ── زائران ───────────────────────────────────────────────────────────

    def _ensure_guests(self, count: int) -> list[User]:
        """ساخت (یا یافتن) زائران نمونه با پروفایل کامل."""
        guests: list[User] = []
        for index, (phone, first, last, province, city) in enumerate(DEMO_GUESTS[:count]):
            user, _created = User.objects.get_or_create(
                phone=phone, defaults={"role": UserRole.GUEST}
            )
            if not user.is_profile_complete:
                user.complete_profile(
                    first_name=first,
                    last_name=last,
                    province=province,
                    city=city,
                    national_id=_demo_national_id(f"{NATIONAL_ID_SEED_PREFIX}{index:02d}00001"),
                )
            guests.append(user)
        return guests

    # ── رزروها ──────────────────────────────────────────────────────────

    def _ensure_reservations(self, guests: list[User], units: list[Unit], count: int) -> int:
        """ثبت رزروهای نمونه در روزهای پیش‌رو — یک رزرو برای هر زائر."""
        if not guests:
            return 0
        today = timezone.localdate()
        created = 0
        for index, guest in enumerate(guests[:count]):
            if selectors.active_count(guest) > 0:
                continue
            unit = units[index % len(units)]
            start, end = _period(
                today + timedelta(days=index + 1), cells=BASE_CELLS + index % CELLS_VARIETY
            )
            if _occupied(unit, start, end):
                continue
            reservation = create_reservation(
                guest=guest,
                unit=unit,
                start=start,
                end=end,
                guests_count=1 + index % MAX_GUESTS_PER_RESERVATION,
                source=ReservationSource.WEB,
                expected_arrival=DEFAULT_EXPECTED_ARRIVAL,
                guest_note=DEMO_NOTE if index % CELLS_VARIETY == 0 else "",
            )
            created += 1
            self._apply_variety(reservation, guest, start, end, index=index)
        return created

    def _apply_variety(
        self,
        reservation: Reservation,
        guest: User,
        start: datetime,
        end: datetime,
        *,
        index: int,
    ) -> None:
        """تنوع وضعیت‌ها: یک لغو، و یک پذیرش اگر بازه جاریِ کارمند باشد."""
        if index == CANCELLED_INDEX:
            cancel_reservation(reservation=reservation, by=guest, reason="تغییر برنامهٔ سفر")
            return
        operator = _operator()
        if index == CHECKED_IN_INDEX and operator is not None and start <= timezone.now() <= end:
            check_in(reservation=reservation, by=operator)

    def _ensure_phone_reservation(self, units: list[Unit]) -> None:
        """یک رزرو تلفنی نمونه (مسیر پذیرش) برای نمایش مسیر کارکنان."""
        operator = _operator()
        if operator is None:
            return
        start, end = _period(
            timezone.localdate() + timedelta(days=PHONE_BOOKING_OFFSET_DAYS), cells=BASE_CELLS
        )
        if _occupied(units[0], start, end):
            return
        guest_exists = User.objects.filter(phone="09120001099").exists()
        if guest_exists and selectors.active_count(User.objects.get(phone="09120001099")) > 0:
            return
        staff_create_reservation(
            phone="09120001099",
            unit=units[0],
            start=start,
            end=end,
            guests_count=BASE_CELLS,
            by=operator,
            first_name="حسن",
            last_name="نوری",
            province=Province.KHORASAN_RAZAVI,
            city="مشهد",
            staff_note="زائر تلفنی؛ کد ملی در پذیرش ثبت می‌شود.",
        )

    def _ensure_block(self, units: list[Unit]) -> None:
        """یک بلوک نگهداری نمونه برای نمایش رنگ «غیرقابل‌رزرو» در تقویم."""
        operator = _operator()
        if operator is None or Block.objects.exists():
            return
        start, end = _period(
            timezone.localdate() + timedelta(days=BLOCK_OFFSET_DAYS), cells=BASE_CELLS
        )
        if _occupied(units[-1], start, end):
            return
        create_block(
            start=start,
            end=end,
            kind="maintenance",
            reason=BLOCK_REASON,
            unit=units[-1],
            actor=operator,
        )


# ── کمکی‌ها ──────────────────────────────────────────────────────────────


def _period(day: date, *, cells: int) -> tuple[datetime, datetime]:
    """ساخت بازهٔ روی‌شبکه از یک روز محلی (``cells`` نیم‌روز از سلول روزانه)."""
    first = cell_at_boundary(cells_for_day(day)[0].start).index
    return period_from_index(first, cells)


def _demo_national_id(seed: str) -> str:
    """ساخت کد ملی معتبر برای دادهٔ نمایشی (با رقم کنترل رسمی).

    این تابع عمداً این‌جاست و نه در کد تولیدی: سامانه هرگز کد ملی «نمی‌سازد»،
    فقط اعتبارسنجی می‌کند. دادهٔ نمایشی اما باید از اعتبارسنجی رد شود.
    """
    digits = "".join(character for character in seed if character.isdigit())[:9].ljust(9, "0")
    total = sum(int(digit) * (10 - position) for position, digit in enumerate(digits, start=1))
    remainder = total % 11
    check = remainder if remainder < CHECK_DIGIT_PIVOT else 11 - remainder
    return f"{digits}{check}"


def _operator() -> User | None:
    """یافتن یک کارمند فعال برای ثبت کنش‌های نمونه."""
    return (
        User.objects.filter(role=UserRole.MANAGER).first()
        or User.objects.filter(role=UserRole.OPERATOR).first()
    )


def _occupied(unit: Unit, start: datetime, end: datetime) -> bool:
    """آیا بازهٔ درخواستی هم‌اکنون اشغال است؟"""
    return selectors.active_conflicts(unit=unit, start=start, end=end).exists()
