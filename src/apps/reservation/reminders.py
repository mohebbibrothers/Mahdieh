"""یادآور پیش از پذیرش — «عدم مراجعه» را با یک پیامک به‌هنگام کم می‌کنیم.

چرا این ماژول؟ سانس‌های زائرسرا رایگان و پرتقاضا هستند؛ زائری که فراموش می‌کند
یا دیر می‌رسد، سانسی را می‌سوزاند که زائر دیگری در صف انتظارش بود. یک پیامک
ساده، پیش از آغاز اقامت، این زیان را کم می‌کند — و سیاستش از قبل در محصول هست:
``notification.reminder_lead_hours`` (پیش‌فرض ۲۴ ساعت).

سه قاعدهٔ طراحی:
    ۱. **بی‌هنگام نفرست**: در ساعات سکوت (``notification.quiet_hours_*``) هیچ
       پیامکی نمی‌رود؛ فرمان دوره‌ای ساعت بعد دوباره تلاش می‌کند.
    ۲. **دو بار نفرست**: کلید یکتایی ``reminder:<id>`` تضمین می‌کند اجرای
       دوبارهٔ فرمان (یا بازپخش) پیام تکراری نمی‌سازد.
    ۳. **شبکه، جریان را نمی‌شکند**: خروجی همیشه گزارش است؛ خطای یک رکورد، بقیهٔ
       فهرست را متوقف نمی‌کند (هم‌راستا با ``send_sms`` که هرگز استثنا نمی‌دهد).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from django.utils import timezone
import structlog

from apps.core.jalali import clock_label, format_jalali
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.models import SmsDelivery
from apps.notifications.services import send_sms
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import policy_int, quiet_hours

logger = structlog.get_logger(__name__)

DEFAULT_LIMIT: Final[int] = 50
DEDUP_PREFIX: Final[str] = "reminder"
LEAD_POLICY_KEY: Final[str] = "notification.reminder_lead_hours"
DELIVERED_STATUSES: Final[frozenset[str]] = frozenset({SmsStatus.SENT, SmsStatus.SUPPRESSED})
# سقف بررسی هر اجرا: سه برابر سقف ارسال، تا اگر بیشترشان یادآوری‌شده بودند هم
# فهرست زنده پر شود بی‌آنکه پرسمان بی‌کران شود.
SCAN_FACTOR: Final[int] = 3


def dedup_key(reservation_id: Any) -> str:
    """کلید یکتایی یادآور یک رزرو — تنها یک یادآور برای هر اقامت."""
    return f"{DEDUP_PREFIX}:{reservation_id}"


def lead_hours() -> int:
    """پنجرهٔ یادآوری از سیاست زنده (صفر یا منفی ⇒ یادآور خاموش)."""
    return max(0, policy_int(LEAD_POLICY_KEY))


def reminder_context(reservation: Reservation) -> dict[str, Any]:
    """متغیرهای الگوی «یادآور» — همان‌ها که کاتالوگ الگو اعلام کرده است.

    کاتالوگ ``patterns.MISSING_TEMPLATES[booking_reminder]`` سه متغیر می‌خواهد:
    ``full_name``، ``unit`` و ``date``. بقیهٔ کلیدها برای متن محلی (دفتر تحویل و
    آداپتور کنسول) است، پس روز رسیدن کد الگو هیچ فراخوانی «کم‌متغیر» نیست.
    """
    start = timezone.localtime(reservation.starts_at)
    return {
        "full_name": reservation.guest.display_name,
        "unit": reservation.unit.title,
        "date": format_jalali(start),
        "code": reservation.code,
        "check_in": f"{format_jalali(start)} — ساعت {clock_label(start)}",
        "check_out": format_jalali(timezone.localtime(reservation.ends_at)),
        "guests": reservation.guests_count,
    }


@dataclass(slots=True)
class ReminderRun:
    """نتیجهٔ یک اجرای یادآوری — برای گزارش فرمان و پایش."""

    examined: int = 0
    sent: int = 0
    blocked: int = 0
    already: int = 0
    quiet: bool = False
    lead_hours: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """خلاصهٔ خوانا برای خروجی فرمان."""
        if self.quiet:
            return "ساعات سکوت است؛ یادآوری فرستاده نشد (اجرای بعدی تلاش می‌کند)."
        return (
            f"یادآور: {self.sent} فرستاده‌شده · {self.blocked} مسدود · "
            f"{self.already} پیش‌تر یادآوری‌شده · {self.examined} بررسی‌شده"
        )


def candidates(*, now: datetime, lead: int, limit: int) -> tuple[list[Reservation], int]:
    """رزروهای واجد یادآور در پنجرهٔ پیش‌رو + شمار آن‌هایی که پیش‌تر یادآور گرفته‌اند.

    تنها یک پرسمان می‌زند؛ رزروهای یادآوری‌شده در پایتون کنار گذاشته می‌شوند تا
    فرمان دوره‌ای، پرسان‌های تکراری به پایگاه‌داده نزند.
    """
    if lead <= 0:
        return [], 0
    horizon = now + timedelta(hours=lead)
    queryset = (
        Reservation.objects.filter(
            status__in=ReservationStatus.active_statuses(),
            stay_period__startswith__gte=now,
            stay_period__startswith__lte=horizon,
        )
        .select_related("unit", "guest")
        .order_by("stay_period")
    )
    scan_limit = max(limit, 1) * SCAN_FACTOR if limit else 0
    rows = list(queryset[:scan_limit] if scan_limit else queryset)
    if not rows:
        return [], 0
    reminded = set(
        SmsDelivery.objects.filter(dedup_key__in=[dedup_key(row.id) for row in rows]).values_list(
            "dedup_key", flat=True
        )
    )
    pending = [row for row in rows if dedup_key(row.id) not in reminded]
    already = len(rows) - len(pending)
    return (pending[:limit] if limit else pending), already


def send_reminder(reservation: Reservation) -> SmsDelivery:
    """ارسال یک یادآور (یا بازگرداندن رکورد پیشین، اگر قبلاً رفته باشد)."""
    return send_sms(
        phone=reservation.guest.phone,
        template_key=SmsTemplateKey.BOOKING_REMINDER,
        context=reminder_context(reservation),
        dedup_key=dedup_key(reservation.id),
    )


def run(*, now: datetime | None = None, limit: int = DEFAULT_LIMIT) -> ReminderRun:
    """اجرای یک چرخهٔ یادآوری — نقطهٔ ورود فرمان دوره‌ای.

    خروجی همیشه ``ReminderRun`` است؛ هیچ استثنایی بیرون نمی‌زند، چون این تابع از
    تایمر systemd صدا زده می‌شود و شکست یک رکورد نباید کل چرخه را بخواباند.
    """
    moment = now or timezone.now()
    effective_lead = lead_hours()
    report = ReminderRun(lead_hours=effective_lead)
    if quiet_hours(moment):
        report.quiet = True
        return report
    pending, already = candidates(now=moment, lead=effective_lead, limit=limit)
    report.already = already
    for reservation in pending:
        report.examined += 1
        try:
            delivery = send_reminder(reservation)
        except Exception as error:  # فرمان روی یک رکورد بد نمی‌ایستد
            report.skipped.append(f"{reservation.code}: {error}")
            logger.warning("reminder_skipped", code=reservation.code, error=str(error))
            continue
        if delivery.status in DELIVERED_STATUSES:
            report.sent += 1
        elif delivery.status == SmsStatus.BLOCKED:
            report.blocked += 1
        else:
            report.skipped.append(f"{reservation.code}: وضعیت {delivery.status}")
    return report
