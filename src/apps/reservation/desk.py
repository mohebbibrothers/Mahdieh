"""میز پذیرش (P-06) — تصویر زندهٔ شیفت: وضعیت ۵ واحد، دفتر نوبت‌ها، کلیدها و رخدادنگار.

چرا یک «تصویر» یکپارچه و نه چند نقطهٔ جدا؟ پیشخوان هر چند ثانیه تازه می‌شود؛ یک درخواست
سبک با پرس‌وجوهای ثابت (بی N+1) هم سریع‌تر است و هم تصویر **هم‌زمان** می‌دهد — کارت واحد،
کاشی شاخص و ردیف دفتر هرگز از دو لحظهٔ متفاوت حرف نمی‌زنند.

قواعد وضعیت واحد در «همین لحظه» (به ترتیب تقدم):
    ۱. زائر پذیرش‌شده دارد ⇒ ``occupied`` (کلید نزد زائر) — حتی اگر از پایان گذشته باشد
       (``is_overstay`` هشدار می‌دهد).
    ۲. بلوک فعال (تعمیر/رویداد/نظافت اساسی) ⇒ ``maintenance`` (کلید نزد تأسیسات).
    ۳. نوبت نظافت باز ⇒ ``buffer`` (کلید نزد خادم خدمات).
    ۴. ورودی تأییدشدهٔ امروز یا با پنجرهٔ پذیرش باز ⇒ ``expected`` (کلید در قفسه).
    ۵. در غیر این صورت ⇒ ``ready`` (کلید در قفسه؛ نزدیک‌ترین ورود بعدی نشان داده می‌شود).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import re
from typing import Any, Final

from django.db.models import Q
from django.utils import timezone

from apps.core.crypto import blind_index
from apps.core.jalali import clock_label, format_jalali
from apps.core.periods import overlap_q
from apps.identity.models import NATIONAL_ID_NAMESPACE, User
from apps.inventory import housekeeping
from apps.inventory.models import Block, HousekeepingTask, Unit
from apps.inventory.selectors import active_units, blocks_overlapping, day_window
from apps.reservation import selectors
from apps.reservation.certificate import (
    SHORT_CODE_ALPHABET,
    OnlineVerification,
    build_certificate,
    verify_online,
)
from apps.reservation.choices import ReservationEventKind as EventKind, ReservationStatus
from apps.reservation.models import Reservation, ReservationEvent
from apps.reservation.policy import policy_int

#: پس از این‌همه دقیقه از آغاز اقامت (یا ساعت اعلامی ورود)، ورودی «تأخیری» شمرده می‌شود.
LATE_MINUTES: Final[int] = 60
#: پیشخوان هر چند ثانیه تصویر تازه بگیرد (نمای HTMX قدیمی هم ۳۰ ثانیه بود).
POLL_SECONDS: Final[int] = 30
EVENTS_LIMIT: Final[int] = 24
LOOKUP_LIMIT: Final[int] = 8
#: بازهٔ جست‌وجوی کد کوتاه آفلاین — فقط رزروهای نزدیک به امروز (چند ده رکورد، نه کل تاریخ).
SHORT_CODE_WINDOW_DAYS: Final[int] = 3
MORNING_START: Final[time] = time(8, 0)
NIGHT_START: Final[time] = time(20, 0)

LIVE_STATUSES: Final[tuple[str, ...]] = (
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
    ReservationStatus.COMPLETED,
    ReservationStatus.NO_SHOW,
    ReservationStatus.CANCELLED_BY_GUEST,
    ReservationStatus.CANCELLED_BY_STAFF,
)
CANCELLED: Final[frozenset[str]] = frozenset(
    {ReservationStatus.CANCELLED_BY_GUEST, ReservationStatus.CANCELLED_BY_STAFF}
)

UNIT_STATE_LABELS: Final[dict[str, str]] = {
    "occupied": "زائر مقیم (کلید تحویل شد)",
    "expected": "در انتظار ورود زائر",
    "ready": "آمادهٔ تحویل کلید",
    "buffer": "در حال نظافت و آماده‌سازی",
    "maintenance": "مسدود برای تعمیرات",
}
KEY_LABELS: Final[dict[str, str]] = {
    "guest": "نزد زائر",
    "cabinet": "در قفسه",
    "housekeeping": "نزد خدمات",
    "facilities": "نزد تأسیسات",
}
LANE_LABELS: Final[dict[str, str]] = {
    "expected": "در انتظار ورود",
    "delayed": "تأخیر در ورود",
    "checked_in": "پذیرش‌شده · کلید تحویل شد",
    "overstay": "گذشته از زمان تخلیه",
    "checked_out": "تخلیه‌شده",
    "no_show": "عدم‌حضور",
    "cancelled": "لغوشده",
}

_RESERVATION_CODE: Final = re.compile(r"^[A-Za-z]{1,3}-\d{4}-\d{2,6}$")
_SHORT_CODE: Final = re.compile(rf"^[{SHORT_CODE_ALPHABET}]{{8}}$")
_NATIONAL_ID: Final = re.compile(r"^\d{10}$")
_TOKEN_IN_URL: Final = re.compile(r"/verify/([^/?#\s]+)")


# ── شیفت ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Shift:
    """شیفت جاری: روز (۰۸–۲۰) یا شب (۲۰–۰۸ فردا)."""

    slot: str
    label: str
    hours_label: str
    starts_at: datetime
    ends_at: datetime

    @property
    def report_range(self) -> tuple[date, date]:
        """بازهٔ تاریخی گزارش اکسل همین شیفت (شیفت شب دو روز را می‌پوشاند)."""
        return self.starts_at.date(), (self.ends_at - timedelta(seconds=1)).date()

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر."""
        start_day, end_day = self.report_range
        return {
            "slot": self.slot,
            "label": self.label,
            "hours_label": self.hours_label,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "report_from": start_day.isoformat(),
            "report_to": end_day.isoformat(),
        }


def current_shift(now: datetime) -> Shift:
    """شیفت در برگیرندهٔ ``now`` به وقت محلی."""
    local = timezone.localtime(now)
    zone = local.tzinfo
    today = local.date()
    morning = datetime.combine(today, MORNING_START, tzinfo=zone)
    night = datetime.combine(today, NIGHT_START, tzinfo=zone)
    if morning <= local < night:
        return Shift("morning", "شیفت روز", "۰۸:۰۰ تا ۲۰:۰۰", morning, night)
    start = night if local >= night else night - timedelta(days=1)
    return Shift("night", "شیفت شب", "۲۰:۰۰ تا ۰۸:۰۰ بامداد", start, start + timedelta(hours=12))


# ── ردیف دفتر ───────────────────────────────────────────────────────────────


def _local_dt(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=timezone.get_current_timezone())


def due_arrival(reservation: Reservation) -> datetime:
    """لحظه‌ای که انتظار ورود می‌رود: ساعت اعلامی زائر (اگر پس از آغاز است) وگرنه آغاز اقامت."""
    start = reservation.starts_at
    if reservation.expected_arrival is None:
        return start
    declared = _local_dt(timezone.localtime(start).date(), reservation.expected_arrival)
    return max(start, declared)


def lane_of(reservation: Reservation, *, now: datetime) -> str:
    """مسیر دفتر یک رزرو در همین لحظه."""
    status = reservation.status
    if status == ReservationStatus.CHECKED_IN:
        return "overstay" if now >= reservation.ends_at else "checked_in"
    if status == ReservationStatus.COMPLETED:
        return "checked_out"
    if status == ReservationStatus.NO_SHOW:
        return "no_show"
    if status in CANCELLED:
        return "cancelled"
    late = now >= due_arrival(reservation) + timedelta(minutes=LATE_MINUTES)
    return "delayed" if late else "expected"


def _lane_label(reservation: Reservation, lane: str) -> str:
    """برچسب مسیر؛ پروندهٔ «پایان‌یافته» بی هیچ پذیرشی، «تخلیه‌شده» خوانده نمی‌شود."""
    if lane == "checked_out" and reservation.checked_in_at is None:
        return "بسته‌شده · بی ثبت پذیرش"
    return LANE_LABELS[lane]


@dataclass(frozen=True, slots=True)
class DeskRules:
    """قواعد زمانی پذیرش و عدم‌حضور — یک بار از سیاست خوانده می‌شود."""

    early_hours: int
    grace_hours: int

    @classmethod
    def load(cls) -> DeskRules:
        """خواندن سیاست‌های جاری."""
        return cls(
            early_hours=policy_int("reservation.checkin_early_hours"),
            grace_hours=policy_int("reservation.no_show_grace_hours"),
        )


def desk_flags(reservation: Reservation, *, now: datetime, rules: DeskRules) -> dict[str, Any]:
    """پرچم‌های عملیاتی ردیف — دکمه‌های پیشخوان دقیقاً همان قواعد سرویس را آینه می‌کنند."""
    confirmed = reservation.status == ReservationStatus.CONFIRMED
    opens_at = reservation.starts_at - timedelta(hours=rules.early_hours)
    no_show_from = reservation.starts_at + timedelta(hours=rules.grace_hours)
    lane = lane_of(reservation, now=now)
    guest: User = reservation.guest
    return {
        "lane": lane,
        "lane_label": _lane_label(reservation, lane),
        "due_at": due_arrival(reservation).isoformat(),
        "due_label": clock_label(due_arrival(reservation)),
        "start_label": f"{format_jalali(reservation.starts_at, with_weekday=True)} · "
        f"{clock_label(reservation.starts_at)}",
        "end_label": f"{format_jalali(reservation.ends_at, with_weekday=True)} · "
        f"{clock_label(reservation.ends_at)}",
        "check_in_opens_at": opens_at.isoformat(),
        "can_check_in": confirmed and opens_at <= now < reservation.ends_at,
        "can_check_out": reservation.status == ReservationStatus.CHECKED_IN,
        "no_show_from": no_show_from.isoformat(),
        "can_no_show": confirmed and now >= no_show_from,
        "can_cancel": confirmed,
        "can_move": confirmed,
        "is_overstay": lane == "overstay",
        "guest_city": guest.city,
        "guest_national_id_masked": guest.masked_national_id,
        "guest_document": guest.id_document_type,
        "short_code": build_certificate(reservation).short_code if confirmed else "",
    }


# ── واحدها ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class UnitCard:
    """وضعیت یک واحد در همین لحظه."""

    unit: Unit
    state: str
    current: Reservation | None = None
    arrival: Reservation | None = None
    block: Block | None = None
    task: HousekeepingTask | None = None

    @property
    def key_location(self) -> str:
        """جای کلید فیزیکی."""
        return {
            "occupied": "guest",
            "buffer": "housekeeping",
            "maintenance": "facilities",
        }.get(self.state, "cabinet")


def _block_for(unit: Unit, blocks: Iterable[Block], now: datetime) -> Block | None:
    for block in blocks:
        if block.starts_at <= now < block.ends_at and block.unit_id in (None, unit.pk):
            return block
    return None


def unit_cards(
    *,
    units: list[Unit],
    rows: list[Reservation],
    upcoming: list[Reservation],
    blocks: list[Block],
    tasks: dict[str, HousekeepingTask],
    now: datetime,
    day_end: datetime,
    rules: DeskRules,
) -> list[UnitCard]:
    """کارت هر واحد بر پایهٔ قواعد تقدم سرشناسه."""
    cards: list[UnitCard] = []
    for unit in units:
        mine = [r for r in rows if r.unit_id == unit.pk]
        current = next(
            (r for r in reversed(mine) if r.status == ReservationStatus.CHECKED_IN), None
        )
        pending = [
            r
            for r in [*mine, *(u for u in upcoming if u.unit_id == unit.pk)]
            if r.status == ReservationStatus.CONFIRMED and r.ends_at > now
        ]
        pending.sort(key=lambda r: r.starts_at)
        arrival = pending[0] if pending else None
        block = _block_for(unit, blocks, now)
        task = tasks.get(unit.code)
        window_open = arrival is not None and (
            arrival.starts_at < day_end
            or arrival.starts_at - timedelta(hours=rules.early_hours) <= now
        )
        if current is not None:
            state = "occupied"
        elif block is not None:
            state = "maintenance"
        elif task is not None:
            state = "buffer"
        elif window_open:
            state = "expected"
        else:
            state = "ready"
        cards.append(UnitCard(unit, state, current, arrival, block, task))
    return cards


# ── رخدادنگار ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeskEvent:
    """یک سطر رخدادنگار شیفت."""

    at: datetime
    kind: str
    text: str
    actor: str
    unit_code: str = ""
    reservation_code: str = ""
    tone: str = "neutral"
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر."""
        return {
            "at": self.at.isoformat(),
            "clock": clock_label(self.at),
            "kind": self.kind,
            "text": self.text,
            "actor": self.actor,
            "unit_code": self.unit_code,
            "reservation_code": self.reservation_code,
            "tone": self.tone,
        }


_EVENT_TONES: Final[dict[str, str]] = {
    EventKind.CHECKED_IN: "success",
    EventKind.CHECKED_OUT: "info",
    EventKind.NO_SHOW: "warning",
    EventKind.CANCELLED: "danger",
    EventKind.MOVED: "info",
    EventKind.CREATED: "neutral",
    EventKind.NOTE: "neutral",
    # بستن خودکار یعنی پیشخوان کاری را ثبت نکرده بود ⇒ هشدار تا خادم وضعیت واحد را بازبینی کند
    EventKind.SETTLED: "warning",
}


#: متن رخدادنگار هر نوع رخداد؛ جای‌نگهدارها: ‎{who} {unit} {code} {guests}‎.
_EVENT_TEXTS: Final[dict[str, str]] = {
    EventKind.CHECKED_IN: "پذیرش {who} ({guests} نفر) در واحد {unit} و تحویل کلید.",
    EventKind.CHECKED_OUT: "تخلیهٔ واحد {unit} توسط {who} و بازگشت کلید.",
    EventKind.NO_SHOW: "ثبت عدم‌حضور {who} برای واحد {unit}.",
    EventKind.CANCELLED: "لغو رزرو {code} ({who}) و آزادشدن سانس {unit}.",
    EventKind.MOVED: "جابه‌جایی رزرو {code} ({who}) به واحد {unit}.",
    EventKind.CREATED: "ثبت رزرو تازهٔ {code} برای {who} در واحد {unit}.",
    EventKind.NOTE: "یادداشت داخلی برای {code}.",
    EventKind.DETAILS: "زائر {who} هماهنگی ورود رزرو {code} را به‌روز کرد.",
}
#: بستن خودکار، به‌تفکیک حالت (‎metadata.mode‎) — پیشخوان باید بداند چه چیزی ثبت نشده بود.
_SETTLED_TEXTS: Final[dict[str, str]] = {
    "checked_out": (
        "پروندهٔ {code} ({who}) پس از پایان اقامت خودکار بسته شد؛ "
        "تخلیهٔ واحد {unit} ثبت نشده بود — وضعیت و کلید واحد را بازبینی کنید."
    ),
    "unattended": (
        "پروندهٔ {code} ({who}) پس از پایان اقامت خودکار بسته شد؛ "
        "پذیرش حضوری در واحد {unit} ثبت نشده بود."
    ),
}
_REASONED: Final[frozenset[str]] = frozenset(
    {EventKind.CANCELLED, EventKind.NO_SHOW, EventKind.MOVED, EventKind.NOTE}
)


def _event_text(event: ReservationEvent) -> str:
    reservation = event.reservation
    kind = event.kind
    if kind == EventKind.SETTLED:
        mode = str((event.metadata or {}).get("mode") or "unattended")
        template = _SETTLED_TEXTS.get(mode, _SETTLED_TEXTS["unattended"])
    else:
        template = _EVENT_TEXTS.get(kind, f"{event.get_kind_display()} — {{code}}.")
    text = template.format(
        who=reservation.guest.display_name,
        unit=reservation.unit.code,
        code=reservation.code,
        guests=reservation.guests_count,
    )
    reason = (event.reason or "").strip()
    if reason and kind in _REASONED:
        text = f"{text} «{reason[:120]}»"
    return text


def recent_events(*, since: datetime, limit: int = EVENTS_LIMIT) -> list[DeskEvent]:
    """رخدادهای رزرو، نظافت و بلوک از ``since`` — تازه‌ترین در بالا."""
    items: list[DeskEvent] = []
    events = (
        ReservationEvent.objects.filter(created_at__gte=since)
        .exclude(kind__in=[EventKind.EXPIRED, EventKind.CONFIRMED])
        .select_related("reservation", "reservation__unit", "reservation__guest", "actor")
        .order_by("-created_at")[: limit * 2]
    )
    for event in events:
        actor = event.actor.display_name if event.actor else (event.actor_label or "سامانه")
        items.append(
            DeskEvent(
                at=event.created_at,
                kind=f"reservation.{event.kind}",
                text=_event_text(event),
                actor=actor,
                unit_code=event.reservation.unit.code,
                reservation_code=event.reservation.code,
                tone=_EVENT_TONES.get(event.kind, "neutral"),
            )
        )
    for task in housekeeping.recent_tasks(since=since):
        if task.started_at >= since:
            items.append(
                DeskEvent(
                    at=task.started_at,
                    kind="housekeeping.started",
                    text=f"واحد {task.unit.code} به {task.assignee} برای نظافت سپرده شد.",
                    actor=task.started_by.display_name if task.started_by else "سامانه",
                    unit_code=task.unit.code,
                    reservation_code=task.reservation_code,
                    tone="warning",
                )
            )
        if task.completed_at is not None and task.completed_at >= since:
            items.append(
                DeskEvent(
                    at=task.completed_at,
                    kind="housekeeping.completed",
                    text=f"نظافت واحد {task.unit.code} پایان یافت؛ آمادهٔ تحویل کلید.",
                    actor=task.completed_by.display_name if task.completed_by else "سامانه",
                    unit_code=task.unit.code,
                    tone="success",
                )
            )
    blocks = Block.objects.filter(created_at__gte=since, unit__isnull=False).select_related(
        "unit", "created_by"
    )
    for block in blocks:
        code = block.unit.code if block.unit else "همهٔ واحدها"
        items.append(
            DeskEvent(
                at=block.created_at,
                kind="block.created",
                text=f"واحد {code} مسدود شد: {block.reason[:120]}",
                actor=block.created_by.display_name if block.created_by else "سامانه",
                unit_code=block.unit.code if block.unit else "",
                tone="danger",
            )
        )
    items.sort(key=lambda item: item.at, reverse=True)
    return items[:limit]


# ── تصویر کامل ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeskSnapshot:
    """دادهٔ خام تصویر پیشخوان (سریال‌سازی در لایهٔ نما)."""

    now: datetime
    day: date
    shift: Shift
    cards: list[UnitCard]
    ledger: list[Reservation]
    events: list[DeskEvent]
    rules: DeskRules


def snapshot(*, now: datetime | None = None) -> DeskSnapshot:
    """ساخت تصویر زندهٔ پیشخوان با شمار ثابتی پرس‌وجو."""
    moment = now or timezone.now()
    day = timezone.localtime(moment).date()
    day_start, day_end = day_window(day)
    rules = DeskRules.load()
    shift = current_shift(moment)
    rows = list(
        selectors.staff_board(start=day_start, end=day_end).filter(status__in=LIVE_STATUSES)
    )
    # مقیم‌های گذشته از تخلیه (پایانشان پیش از امروز بوده) هم باید دیده شوند.
    seen = {r.pk for r in rows}
    stale = selectors.staff_board(start=day_start - timedelta(days=30), end=day_start).filter(
        status=ReservationStatus.CHECKED_IN
    )
    rows.extend(r for r in stale if r.pk not in seen)
    rows.sort(key=lambda r: (r.starts_at, r.ends_at))
    upcoming = list(
        selectors.staff_board(start=day_end, end=day_end + timedelta(days=14)).filter(
            status=ReservationStatus.CONFIRMED
        )
    )
    units = list(active_units())
    blocks = list(blocks_overlapping(start=moment, end=moment + timedelta(seconds=1)))
    cards = unit_cards(
        units=units,
        rows=rows,
        upcoming=upcoming,
        blocks=blocks,
        tasks=housekeeping.open_tasks_by_unit(),
        now=moment,
        day_end=day_end,
        rules=rules,
    )
    events = recent_events(since=min(shift.starts_at, moment - timedelta(hours=12)))
    return DeskSnapshot(moment, day, shift, cards, rows, events, rules)


def kpis(snap: DeskSnapshot) -> dict[str, int]:
    """شاخص‌های کاشی‌ها — همه از همان تصویر."""
    states = [card.state for card in snap.cards]
    lanes = [lane_of(r, now=snap.now) for r in snap.ledger]
    _, day_end = day_window(snap.day)
    return {
        "occupied": states.count("occupied"),
        "ready": states.count("ready"),
        "buffer": states.count("buffer"),
        "expected": states.count("expected"),
        "maintenance": states.count("maintenance"),
        "guests_in_house": sum(
            r.guests_count for r in snap.ledger if r.status == ReservationStatus.CHECKED_IN
        ),
        "arrivals_pending": lanes.count("expected") + lanes.count("delayed"),
        "delayed": lanes.count("delayed") + lanes.count("overstay"),
        "departures_today": sum(
            1
            for r in snap.ledger
            if r.status == ReservationStatus.CHECKED_IN and r.ends_at <= day_end
        ),
        "checked_in_today": sum(
            1
            for r in snap.ledger
            if r.checked_in_at is not None
            and timezone.localtime(r.checked_in_at).date() == snap.day
        ),
    }


# ── جست‌وجوی پیشخوان ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LookupResult:
    """نتیجهٔ جست‌وجوی اسکنر/دستی."""

    kind: str
    matches: list[Reservation]
    certificate: OnlineVerification | None = None


def _active_window(now: datetime) -> Q:
    return overlap_q(
        "starts_at",
        "ends_at",
        now - timedelta(days=2),
        now + timedelta(days=SHORT_CODE_WINDOW_DAYS),
    ) & Q(status__in=[ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN])


def normalize_query(raw: str) -> str:
    """ارقام فارسی/عربی ⇒ لاتین، حذف فاصله‌های اضافه."""
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return " ".join((raw or "").translate(table).split())[:200]


def lookup(raw: str, *, now: datetime | None = None) -> LookupResult:
    """تشخیص نوع ورودی (QR/پیوند گواهی، کد رزرو، کد آفلاین، کد ملی، تلفن/نام) و یافتن رزرو."""
    moment = now or timezone.now()
    text = normalize_query(raw)
    if not text:
        return LookupResult("empty", [])
    token_match = _TOKEN_IN_URL.search(text)
    token = token_match.group(1) if token_match else text
    if "." in token and " " not in token:
        result = verify_online(token)
        found = [result.reservation] if result.reservation is not None else []
        return LookupResult("certificate", found, result)
    compact = text.replace("-", "").replace(" ", "").upper()
    if _RESERVATION_CODE.match(text):
        found_code = selectors.reservation_by_code(text)
        return LookupResult("code", [found_code] if found_code else [])
    if _SHORT_CODE.match(compact) and not compact.isdigit():
        candidates = selectors.staff_board(
            start=moment - timedelta(days=2), end=moment + timedelta(days=SHORT_CODE_WINDOW_DAYS)
        ).filter(status=ReservationStatus.CONFIRMED)
        found_short = [r for r in candidates if build_certificate(r).short_code == compact]
        return LookupResult("short_code", found_short[:LOOKUP_LIMIT])
    if _NATIONAL_ID.match(compact):
        blind = blind_index(compact, namespace=NATIONAL_ID_NAMESPACE)
        rows = (
            Reservation.objects.select_related("unit", "unit__floor", "guest", "created_by")
            .filter(_active_window(moment), guest__national_id_blind=blind)
            .order_by("starts_at")
        )
        return LookupResult("national_id", list(rows[:LOOKUP_LIMIT]))
    rows = (
        selectors.staff_board(
            start=moment - timedelta(days=2),
            end=moment + timedelta(days=SHORT_CODE_WINDOW_DAYS),
            filters=selectors.BoardFilter(query=text),
        )
        .filter(status__in=[ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN])
        .order_by("starts_at")
    )
    return LookupResult("query", list(rows[:LOOKUP_LIMIT]))
