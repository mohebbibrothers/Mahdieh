"""وضعیت حساب زائر و پیش‌نمایش لغو — «پیش از کلیک، بدان چه می‌شود».

دو پرسش که فرانت باید **بدون آزمون‌وخطا** پاسخ بدهد:

    ۱. «الان می‌توانم رزرو کنم؟ اگر نه، چرا و تا کی؟» ⇒ ``guest_standing``
       (سهمیه‌ها، خنک‌شدن پس از لغو دیرهنگام، محرومیت عدم‌حضور، تکمیل پروفایل).
    ۲. «اگر این رزرو را لغو کنم چه می‌شود؟» ⇒ ``cancel_preview``
       (رایگان یا دیرهنگام، و اگر دیرهنگام، تا چه زمانی رزرو تازه بسته می‌ماند).

هر دو **فقط می‌خوانند** و از همان قواعدی تغذیه می‌کنند که موتور رزرو اجرا
می‌کند (``policy_int`` + ``selectors``)؛ پس پیش‌نمایش هرگز با نتیجهٔ واقعی
اختلاف ندارد. ``services.cancel_reservation`` هم «دیرهنگام بودن» را از همین‌جا
می‌پرسد تا یک قاعده فقط یک پیاده‌سازی داشته باشد.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.core.jalali import clock_label, format_jalali
from apps.core.text import to_persian_digits as fa
from apps.identity.models import User
from apps.reservation import selectors
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import policy_int

YEAR_DAYS = 365


def _moment_label(moment: datetime) -> str:
    """برچسب فارسی یک لحظه: «۱۴۰۵/۰۷/۲۵ ساعت ۲۰:۰۰»."""
    local = timezone.localtime(moment)
    return f"{format_jalali(local.date(), numeric=True)} ساعت {clock_label(local)}"


def _iso(moment: datetime | None) -> str | None:
    """ISO 8601 به وقت محلی سایت (یا ``None``)."""
    return timezone.localtime(moment).isoformat() if moment is not None else None


@dataclass(frozen=True, slots=True)
class Quota:
    """یک سهمیهٔ شمارشی: مصرف‌شده از سقف."""

    used: int
    limit: int
    window_days: int | None = None

    @property
    def remaining(self) -> int:
        """باقی‌ماندهٔ سهمیه (هرگز منفی نمی‌شود)."""
        return max(0, self.limit - self.used)

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر."""
        return {
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "window_days": self.window_days,
        }


@dataclass(frozen=True, slots=True)
class Standing:
    """تصویر کامل «توان رزرو» یک زائر در همین لحظه."""

    can_book: bool
    profile_complete: bool
    active: Quota
    window: Quota
    year: Quota
    cooldown_until: datetime | None
    suspended_until: datetime | None
    blockers: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def next_eligible_at(self) -> datetime | None:
        """زودترین لحظه‌ای که محدودیت‌های **زمان‌دار** برداشته می‌شوند."""
        moments = [m for m in (self.cooldown_until, self.suspended_until) if m is not None]
        return max(moments) if moments else None

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر برای API."""
        return {
            "can_book": self.can_book,
            "profile_complete": self.profile_complete,
            "active": self.active.as_dict(),
            "window": self.window.as_dict(),
            "year": self.year.as_dict(),
            "cooldown_until": _iso(self.cooldown_until),
            "suspended_until": _iso(self.suspended_until),
            "next_eligible_at": _iso(self.next_eligible_at),
            "blockers": list(self.blockers),
        }


def _blocker(code: str, message: str) -> dict[str, str]:
    """یک مانع رزرو — همان شکل ``violations`` در پیش‌بینی رزرو."""
    return {"code": code, "message": fa(message)}


def guest_standing(guest: User) -> Standing:
    """وضعیت توان رزرو زائر؛ موانع با همان کدهای ``build_quote``."""
    now = timezone.now()
    active = Quota(
        used=selectors.active_count(guest), limit=policy_int("reservation.max_active_per_user")
    )
    window_days = policy_int("reservation.cells_window_days")
    window = Quota(
        used=selectors.cells_used(guest=guest, start=now - timedelta(days=window_days), end=now),
        limit=policy_int("reservation.max_cells_per_window"),
        window_days=window_days,
    )
    year = Quota(
        used=selectors.cells_used(guest=guest, start=now - timedelta(days=YEAR_DAYS), end=now),
        limit=policy_int("reservation.max_cells_per_year"),
        window_days=YEAR_DAYS,
    )
    cooldown_until = selectors.pending_cooldown_until(
        guest, hours=policy_int("reservation.late_cancel_cooldown_hours")
    )
    suspended_until = selectors.suspended_until(
        guest, days=policy_int("reservation.no_show_suspension_days")
    )
    min_cells = policy_int("reservation.min_cells")

    blockers: list[dict[str, str]] = []
    if not guest.is_profile_complete:
        blockers.append(_blocker("profile_incomplete", "برای رزرو ابتدا پروفایل خود را کامل کنید."))
    if active.remaining == 0:
        blockers.append(
            _blocker(
                "active_limit",
                f"شما {active.used} رزرو فعال دارید و سقف مجاز {active.limit} است.",
            )
        )
    if window.remaining < min_cells:
        blockers.append(
            _blocker(
                "window_quota",
                f"سهمیهٔ {window_days} روزهٔ شما تکمیل شده است "
                f"({window.used} از {window.limit} نیم‌روز).",
            )
        )
    if year.remaining < min_cells:
        blockers.append(
            _blocker("year_quota", f"سهمیهٔ سالانهٔ شما ({year.used} از {year.limit}) پر است.")
        )
    if cooldown_until is not None:
        blockers.append(
            _blocker(
                "cooldown",
                f"پس از لغو دیرهنگام، تا {_moment_label(cooldown_until)} رزرو تازه ممکن نیست.",
            )
        )
    if suspended_until is not None:
        blockers.append(
            _blocker(
                "suspended",
                f"به‌خاطر عدم‌حضور، حساب تا {_moment_label(suspended_until)} محدود است.",
            )
        )
    return Standing(
        can_book=not blockers,
        profile_complete=guest.is_profile_complete,
        active=active,
        window=window,
        year=year,
        cooldown_until=cooldown_until,
        suspended_until=suspended_until,
        blockers=tuple(blockers),
    )


def free_cancel_until(reservation: Reservation) -> datetime:
    """آخرین لحظهٔ لغو رایگان (بدون خنک‌شدن) برای این رزرو."""
    return reservation.starts_at - timedelta(
        hours=policy_int("reservation.cancel_free_before_hours")
    )


def is_late_cancel(reservation: Reservation, *, now: datetime | None = None) -> bool:
    """آیا لغو در این لحظه «دیرهنگام» حساب می‌شود؟ (قاعدهٔ یگانهٔ موتور)."""
    return (now or timezone.now()) > free_cancel_until(reservation)


@dataclass(frozen=True, slots=True)
class CancelPreview:
    """پیامد لغو این رزرو در همین لحظه — پیش از تأیید زائر."""

    cancellable: bool
    late: bool
    free_until: datetime
    cooldown_hours: int
    cooldown_until: datetime | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر برای API."""
        return {
            "cancellable": self.cancellable,
            "late": self.late,
            "free_until": _iso(self.free_until),
            "cooldown_hours": self.cooldown_hours,
            "cooldown_until": _iso(self.cooldown_until),
            "message": self.message,
        }


def cancel_preview(reservation: Reservation, *, now: datetime | None = None) -> CancelPreview:
    """پیش‌نمایش لغو: رایگان یا دیرهنگام، و مهلت خنک‌شدن احتمالی."""
    moment = now or timezone.now()
    until = free_cancel_until(reservation)
    cooldown_hours = policy_int("reservation.late_cancel_cooldown_hours")
    if reservation.status not in ReservationStatus.cancellable_statuses():
        return CancelPreview(
            cancellable=False,
            late=False,
            free_until=until,
            cooldown_hours=cooldown_hours,
            cooldown_until=None,
            message=f"این رزرو در وضعیت «{reservation.status_label()}» قابل لغو نیست.",
        )
    late = moment > until
    if late:
        cooldown_until = moment + timedelta(hours=cooldown_hours)
        message = fa(
            "مهلت لغو رایگان گذشته است. با لغو، سانس آزاد می‌شود ولی تا "
            f"{_moment_label(cooldown_until)} ({cooldown_hours} ساعت) رزرو تازه ممکن نیست."
        )
    else:
        cooldown_until = None
        message = fa(
            f"لغو تا {_moment_label(until)} رایگان است و هیچ محدودیتی برای رزرو بعدی ندارد."
        )
    return CancelPreview(
        cancellable=True,
        late=late,
        free_until=until,
        cooldown_hours=cooldown_hours,
        cooldown_until=cooldown_until,
        message=message,
    )
