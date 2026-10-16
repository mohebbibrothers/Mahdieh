"""پروندهٔ تقویم (`.ics`) — «رزرو در تقویم خودم باشد».

مسئلهٔ واقعی: زائر رزرو را روی گوشی می‌خواهد؛ اگر مجبور شود تاریخ و ساعت را
دستی در تقویم وارد کند، دیر یا اشتباه یادداشت می‌کند و پذیرش با مهمانِ
«فراموش‌کرده» روبه‌رو می‌شود. پس هر رزرو یک پروندهٔ استاندارد iCalendar دارد.

قواعد پیاده‌سازی (RFC 5545):
    • خطوط با ``CRLF`` پایان می‌یابند و هیچ خطی از ۷۵ بایت فراتر نمی‌رود
      (شکستن روی مرز کاراکتر انجام می‌شود تا متن فارسی خراب نشود).
    • متن ویژگی‌ها ``escape`` می‌شود (``\\``، ``;``، ``,`` و شکست خط).
    • زمان‌ها به UTC (``Z``) نوشته می‌شوند؛ وابستگی به ``VTIMEZONE`` لازم نیست
      و همهٔ تقویم‌ها یکسان می‌فهمند.
    • یادآور (``VALARM``) پیش‌فرض ۱۲ ساعت پیش از ورود تنظیم می‌شود.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode

from django.utils import timezone

from apps.core.site import absolute_url

PRODID: Final[str] = "-//Mahdieh Pilgrim House//Reservation Calendar//FA"
CALENDAR_NAME: Final[str] = "رزروهای زائرسرای مهدیه"
CONTENT_TYPE: Final[str] = "text/calendar; charset=utf-8"
LINE_LIMIT: Final[int] = 75
REMINDER_HOURS: Final[int] = 12
EVENT_DURATION_FALLBACK_HOURS: Final[int] = 12
GOOGLE_CALENDAR_ENDPOINT: Final[str] = "https://calendar.google.com/calendar/render"


def escape_text(value: Any) -> str:
    """فرار دادن متن برای مقدار یک ویژگی iCalendar (RFC 5545، بند ۳٫۳٫۱۱)."""
    text = str(value or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def format_utc(moment: datetime) -> str:
    """قالب UTC استاندارد تقویم: ``YYYYMMDDTHHMMSSZ``."""
    aware = moment if timezone.is_aware(moment) else timezone.make_aware(moment)
    return aware.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def fold_line(line: str) -> list[str]:
    """شکستن یک خط به قطعه‌های حداکثر ۷۵ بایتی بدون آسیب به کاراکترهای چندبایتی.

    پیوست هر قطعه (به‌جز اولی) با یک فاصله شروع می‌شود — قاعدهٔ تاکردن RFC 5545.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= LINE_LIMIT:
        return [line]
    chunks: list[str] = []
    current = b""
    for char in line:
        payload = char.encode("utf-8")
        limit = LINE_LIMIT if not chunks else LINE_LIMIT - 1
        if len(current) + len(payload) > limit:
            chunks.append(current.decode("utf-8"))
            current = b""
        current += payload
    if current:
        chunks.append(current.decode("utf-8"))
    return [chunks[0], *[f" {chunk}" for chunk in chunks[1:]]]


def fold(properties: Iterable[str]) -> str:
    """سرهم‌بندی ویژگی‌ها به متن نهایی تقویم با ``CRLF``."""
    lines: list[str] = []
    for prop in properties:
        lines.extend(fold_line(prop))
    return "\r\n".join(lines) + "\r\n"


def _event_uid(reservation: Any) -> str:
    """شناسهٔ پایدار رخداد — با کد رزرو یکتا و قابل‌ردیابی در تقویم."""
    host = absolute_url("/").split("//")[-1].rstrip("/") or "mahdieh.local"
    return f"{reservation.code}@{host}"


def _summary(reservation: Any) -> str:
    """عنوان کوتاه رخداد: «اقامت در زائرسرای مهدیه — واحد B1»."""
    return f"اقامت در زائرسرای مهدیه — واحد {reservation.unit.code}"


def _location(reservation: Any) -> str:
    """محل رخداد — طبقه و واحد (نشانی دقیق در پذیرش داده می‌شود)."""
    return f"زائرسرای مهدیه — طبقهٔ {reservation.unit.floor_number}، واحد {reservation.unit.code}"


def _description(reservation: Any) -> str:
    """توضیح رخداد: کد رزرو، تعداد نفرات، سیاست لغو و پیوند تأیید گواهی."""
    parts = [
        f"کد رزرو: {reservation.code}",
        f"واحد: {reservation.unit.title} ({reservation.unit.code})",
        f"تعداد نفرات: {reservation.guests_count}",
        f"مدت اقامت: {reservation.duration_label}",
        "تحویل واحد پس از نظافت انجام می‌شود؛ برای تغییر یا لغو با پذیرش تماس بگیرید.",
        f"صفحهٔ رزرو: {absolute_url(f'/reservations/{reservation.code}/')}",
    ]
    return "\n".join(parts)


def _alarm() -> list[str]:
    """یادآور پیش‌فرض: ۱۲ ساعت پیش از زمان ورود، در تقویم مهمان."""
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"TRIGGER:-PT{REMINDER_HOURS}H",
        f"DESCRIPTION:{escape_text('یادآوری اقامت در زائرسرای مهدیه')}",
        "END:VALARM",
    ]


def event_lines(
    reservation: Any, *, dtstamp: datetime | None = None, with_alarm: bool = True
) -> list[str]:
    """ویژگی‌های یک ``VEVENT`` برای یک رزرو (بدون خطوط پوشش تقویم)."""
    starts_at: datetime = reservation.starts_at
    ends_at: datetime = reservation.ends_at
    if ends_at <= starts_at:  # pragma: no cover - تضمین مدل؛ سد ایمنی تقویم
        ends_at = starts_at + timedelta(hours=EVENT_DURATION_FALLBACK_HOURS)
    stamp = dtstamp or timezone.now()
    lines = [
        "BEGIN:VEVENT",
        f"UID:{escape_text(_event_uid(reservation))}",
        f"DTSTAMP:{format_utc(stamp)}",
        f"DTSTART:{format_utc(starts_at)}",
        f"DTEND:{format_utc(ends_at)}",
        f"SUMMARY:{escape_text(_summary(reservation))}",
        f"DESCRIPTION:{escape_text(_description(reservation))}",
        f"LOCATION:{escape_text(_location(reservation))}",
        f"URL:{absolute_url(f'/reservations/{reservation.code}/')}",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "SEQUENCE:0",
        "CATEGORIES:زائرسرا,اقامت",
    ]
    if with_alarm:
        lines.extend(_alarm())
    lines.append("END:VEVENT")
    return lines


def reservation_ics(reservation: Any, *, with_alarm: bool = True) -> str:
    """پروندهٔ تقویم یک رزرو (یک رخداد)."""
    return ics_for([reservation], with_alarm=with_alarm)


def ics_for(reservations: Iterable[Any], *, with_alarm: bool = True) -> str:
    """پروندهٔ تقویم برای چند رزرو — برای «همهٔ رزروهای من» و پنل پذیرش."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(CALENDAR_NAME)}",
    ]
    for reservation in reservations:
        lines.extend(event_lines(reservation, with_alarm=with_alarm))
    lines.append("END:VCALENDAR")
    return fold(lines)


def calendar_file_name(code: str) -> str:
    """نام پروندهٔ تقویم — بر پایهٔ کد رزرو (قابل‌تشخیص در پوشهٔ دانلود)."""
    safe = "".join(char for char in str(code) if char.isalnum() or char in "-_")
    return f"mahdieh-{safe or 'reservation'}.ics"


def google_calendar_url(reservation: Any) -> str:
    """پیوند «افزودن به تقویم گوگل» — برای کسانی که پرونده دانلود نمی‌کنند."""
    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": _summary(reservation),
            "dates": f"{format_utc(reservation.starts_at)}/{format_utc(reservation.ends_at)}",
            "details": _description(reservation),
            "location": _location(reservation),
        }
    )
    return f"{GOOGLE_CALENDAR_ENDPOINT}?{query}"
