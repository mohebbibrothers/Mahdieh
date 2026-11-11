"""API میز پذیرش (P-06) — تصویر زندهٔ شیفت، جست‌وجوی اسکنر و چرخهٔ نظافت واحد.

همهٔ نقطه‌ها فقط برای کارکنانِ گذشته از سد عامل دوم است (``IsStaffRole`` + میان‌افزار
``StaffSecurityMiddleware``). شمارهٔ تلفن در هیچ پاسخی روشن نیست؛ نمایش شمارهٔ کامل
فقط از مسیر جداگانهٔ ``staff/guests/<id>/reveal-phone`` با تأیید تازه و هدف ثبت‌شده است.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.errors import NotFoundError
from apps.core.jalali import clock_label, format_jalali
from apps.core.schema import PROBLEM_403, PROBLEM_404, PROBLEM_409, PROBLEM_422
from apps.identity import mfa
from apps.identity.choices import UserRole
from apps.identity.permissions import IsStaffRole, authenticated_user
from apps.identity.throttles import SensitiveActionThrottle
from apps.inventory import housekeeping
from apps.inventory.choices import BlockKind
from apps.inventory.models import Block, HousekeepingTask, Unit
from apps.inventory.selectors import unit_by_code
from apps.reservation import desk
from apps.reservation.choices import CancelReasonKind
from apps.reservation.models import Reservation
from apps.reservation.serializers import StaffReservationSerializer

# ── سریالایزرها ─────────────────────────────────────────────────────────────


class DeskFlagsSerializer(serializers.Serializer[Any]):
    """پرچم‌های عملیاتی ردیف دفتر — آینهٔ دقیق قواعد سرویس پذیرش/عدم‌حضور."""

    lane = serializers.ChoiceField(choices=list(desk.LANE_LABELS))
    lane_label = serializers.CharField()
    due_at = serializers.DateTimeField()
    due_label = serializers.CharField()
    start_label = serializers.CharField()
    end_label = serializers.CharField()
    check_in_opens_at = serializers.DateTimeField()
    can_check_in = serializers.BooleanField()
    can_check_out = serializers.BooleanField()
    no_show_from = serializers.DateTimeField()
    can_no_show = serializers.BooleanField()
    can_cancel = serializers.BooleanField()
    can_move = serializers.BooleanField()
    is_overstay = serializers.BooleanField()
    guest_city = serializers.CharField()
    guest_national_id_masked = serializers.CharField()
    guest_document = serializers.CharField()
    short_code = serializers.CharField(help_text="کد کوتاه گواهی برای تطبیق با برگهٔ چاپی زائر")


class DeskRowSerializer(StaffReservationSerializer):
    """ردیف دفتر پیشخوان = نمای کارکنان رزرو + پرچم‌های عملیاتی همین لحظه."""

    desk = serializers.SerializerMethodField()

    class Meta(StaffReservationSerializer.Meta):
        fields: tuple[str, ...] = (*StaffReservationSerializer.Meta.fields, "desk")  # type: ignore[assignment]

    @extend_schema_field(DeskFlagsSerializer)
    def get_desk(self, obj: Reservation) -> dict[str, Any]:
        """پرچم‌ها با «اکنون» و قواعد مشترک همین پاسخ."""
        now: datetime = self.context.get("now") or timezone.now()
        rules: desk.DeskRules = self.context.get("rules") or desk.DeskRules.load()
        return desk.desk_flags(obj, now=now, rules=rules)


class DeskBlockSerializer(serializers.Serializer[Any]):
    """بلوک فعال روی واحد (علت مسدودی)."""

    id = serializers.UUIDField()
    kind = serializers.CharField()
    kind_label = serializers.CharField()
    reason = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    ends_label = serializers.CharField()


class HousekeepingSerializer(serializers.Serializer[Any]):
    """نوبت نظافت باز واحد با پیشرفت زمانی."""

    id = serializers.UUIDField()
    assignee = serializers.CharField()
    note = serializers.CharField()
    reservation_code = serializers.CharField()
    started_at = serializers.DateTimeField()
    started_label = serializers.CharField()
    due_at = serializers.DateTimeField()
    due_label = serializers.CharField()
    percent = serializers.IntegerField()
    overdue = serializers.BooleanField()
    remaining_minutes = serializers.IntegerField()


class DeskUnitSerializer(serializers.Serializer[Any]):
    """کارت یک واحد در تابلوی زنده."""

    code = serializers.CharField()
    title = serializers.CharField()
    type_label = serializers.CharField()
    floor_number = serializers.IntegerField()
    floor_title = serializers.CharField()
    capacity_adults = serializers.IntegerField()
    capacity_children = serializers.IntegerField()
    capacity_total = serializers.IntegerField()
    state = serializers.ChoiceField(choices=list(desk.UNIT_STATE_LABELS))
    state_label = serializers.CharField()
    key_location = serializers.ChoiceField(choices=list(desk.KEY_LABELS))
    key_label = serializers.CharField()
    current = DeskRowSerializer(allow_null=True)
    arrival = DeskRowSerializer(allow_null=True)
    block = DeskBlockSerializer(allow_null=True)
    housekeeping = HousekeepingSerializer(allow_null=True)


class DeskShiftSerializer(serializers.Serializer[Any]):
    """شیفت جاری."""

    slot = serializers.ChoiceField(choices=["morning", "night"])
    label = serializers.CharField()  # type: ignore[assignment]
    hours_label = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    report_from = serializers.DateField()
    report_to = serializers.DateField()


class DeskOfficerSerializer(serializers.Serializer[Any]):
    """خادم کشیک (کاربر همین نشست)."""

    display_name = serializers.CharField()
    initials = serializers.CharField()
    role = serializers.CharField()
    role_label = serializers.CharField()
    is_manager = serializers.BooleanField()


class DeskMfaSerializer(serializers.Serializer[Any]):
    """وضعیت عامل دوم نشست — برای نشان «سطح امنیتی» و پنجرهٔ تأیید تازه."""

    verified = serializers.BooleanField()
    method = serializers.CharField()
    step_up_valid_until = serializers.DateTimeField(allow_null=True)
    step_up_seconds = serializers.IntegerField()
    recovery_codes_remaining = serializers.IntegerField()


class DeskEventSerializer(serializers.Serializer[Any]):
    """یک سطر رخدادنگار شیفت."""

    at = serializers.DateTimeField()
    clock = serializers.CharField()
    kind = serializers.CharField()
    text = serializers.CharField()
    actor = serializers.CharField()
    unit_code = serializers.CharField()
    reservation_code = serializers.CharField()
    tone = serializers.ChoiceField(choices=["neutral", "success", "info", "warning", "danger"])


class DeskKpisSerializer(serializers.Serializer[Any]):
    """شاخص‌های کاشی‌ها."""

    occupied = serializers.IntegerField()
    ready = serializers.IntegerField()
    buffer = serializers.IntegerField()
    expected = serializers.IntegerField()
    maintenance = serializers.IntegerField()
    guests_in_house = serializers.IntegerField()
    arrivals_pending = serializers.IntegerField()
    delayed = serializers.IntegerField()
    departures_today = serializers.IntegerField()
    checked_in_today = serializers.IntegerField()


class ChoiceItemSerializer(serializers.Serializer[Any]):
    """یک گزینهٔ فهرست بسته (مقدار ماشینی + برچسب فارسی)."""

    value = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]


class DeskHousekeepingConfigSerializer(serializers.Serializer[Any]):
    """پیکربندی فرم «تحویل به نظافت»."""

    presets = serializers.ListField(child=serializers.IntegerField())
    default_minutes = serializers.IntegerField()
    min_minutes = serializers.IntegerField()
    max_minutes = serializers.IntegerField()
    suggestions = serializers.ListField(child=serializers.CharField())


class DeskWaitlistSerializer(serializers.Serializer[Any]):
    """خلاصهٔ صف انتظار برای دکمهٔ «صف انتظار اضطراری»."""

    waiting = serializers.IntegerField()
    offered = serializers.IntegerField()
    open_offers = serializers.IntegerField()
    expiring_soon = serializers.IntegerField()


class DeskRulesSerializer(serializers.Serializer[Any]):
    """قواعد زمانی پیشخوان."""

    checkin_early_hours = serializers.IntegerField()
    no_show_grace_hours = serializers.IntegerField()
    late_minutes = serializers.IntegerField()


class DeskSerializer(serializers.Serializer[Any]):
    """تصویر کامل پیشخوان (سند)."""

    now = serializers.DateTimeField()
    day = serializers.DateField()
    day_label = serializers.CharField()
    shift = DeskShiftSerializer()
    officer = DeskOfficerSerializer()
    mfa = DeskMfaSerializer()
    units = DeskUnitSerializer(many=True)
    kpis = DeskKpisSerializer()
    ledger = DeskRowSerializer(many=True)
    events = DeskEventSerializer(many=True)
    waitlist = DeskWaitlistSerializer()
    rules = DeskRulesSerializer()
    housekeeping = DeskHousekeepingConfigSerializer()
    block_kinds = ChoiceItemSerializer(many=True)
    cancel_reasons = ChoiceItemSerializer(many=True)
    poll_seconds = serializers.IntegerField()


class CertificateCheckSerializer(serializers.Serializer[Any]):
    """نتیجهٔ تأیید QR/پیوند گواهی در جست‌وجوی پیشخوان."""

    valid = serializers.BooleanField()
    crypto_valid = serializers.BooleanField()
    mismatch = serializers.BooleanField()
    error = serializers.CharField()


class DeskLookupSerializer(serializers.Serializer[Any]):
    """نتیجهٔ جست‌وجوی اسکنر/دستی."""

    kind = serializers.ChoiceField(
        choices=["empty", "certificate", "code", "short_code", "national_id", "query"]
    )
    kind_label = serializers.CharField()
    matches = DeskRowSerializer(many=True)
    certificate = CertificateCheckSerializer(allow_null=True)


class HousekeepingStartSerializer(serializers.Serializer[Any]):
    """ورودی آغاز نظافت دستی واحد."""

    assignee = serializers.CharField(max_length=housekeeping.ASSIGNEE_MAX)
    minutes = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=housekeeping.MIN_MINUTES,
        max_value=housekeeping.MAX_MINUTES,
    )
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)


class DeskUnitResponseSerializer(serializers.Serializer[Any]):
    """پاسخ کنش‌های واحد: کارت تازهٔ همان واحد."""

    unit = DeskUnitSerializer()


LOOKUP_LABELS: dict[str, str] = {
    "empty": "ورودی خالی",
    "certificate": "گواهی اقامت (QR)",
    "code": "کد رزرو",
    "short_code": "کد کوتاه آفلاین گواهی",
    "national_id": "کد ملی سرپرست",
    "query": "نام یا شمارهٔ همراه",
}


# ── ساخت نمای واحد ──────────────────────────────────────────────────────────


def _block_dict(block: Block | None) -> dict[str, Any] | None:
    if block is None:
        return None
    return {
        "id": block.pk,
        "kind": block.kind,
        "kind_label": block.get_kind_display(),
        "reason": block.reason,
        "starts_at": block.starts_at,
        "ends_at": block.ends_at,
        "ends_label": f"{format_jalali(block.ends_at, with_weekday=True)} · "
        f"ساعت {clock_label(block.ends_at)}",
    }


def _task_dict(task: HousekeepingTask | None, now: datetime) -> dict[str, Any] | None:
    if task is None:
        return None
    progress = housekeeping.progress_of(task, now=now)
    return {
        "id": task.pk,
        "assignee": task.assignee,
        "note": task.note,
        "reservation_code": task.reservation_code,
        "started_at": task.started_at,
        "started_label": clock_label(task.started_at),
        "due_at": task.due_at,
        "due_label": clock_label(task.due_at),
        "percent": progress.percent,
        "overdue": progress.overdue,
        "remaining_minutes": progress.remaining_minutes,
    }


def unit_payload(card: desk.UnitCard, *, context: dict[str, Any]) -> dict[str, Any]:
    """نمای کامل یک کارت واحد."""
    unit: Unit = card.unit
    now: datetime = context["now"]

    def row(reservation: Reservation | None) -> dict[str, Any] | None:
        """ردیف دفتر یک رزرو (یا تهی)."""
        return DeskRowSerializer(reservation, context=context).data if reservation else None

    return {
        "code": unit.code,
        "title": unit.title,
        "type_label": unit.type_label,
        "floor_number": unit.floor_number,
        "floor_title": unit.floor.title,
        "capacity_adults": unit.capacity_adults,
        "capacity_children": unit.capacity_children,
        "capacity_total": unit.capacity_total,
        "state": card.state,
        "state_label": desk.UNIT_STATE_LABELS[card.state],
        "key_location": card.key_location,
        "key_label": desk.KEY_LABELS[card.key_location],
        "current": row(card.current),
        "arrival": row(card.arrival),
        "block": _block_dict(card.block),
        "housekeeping": _task_dict(card.task, now),
    }


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "خ"
    if len(parts) == 1:
        return parts[0][:2]
    return f"{parts[0][0]}‌{parts[-1][0]}"


def desk_payload(request: Request, snap: desk.DeskSnapshot) -> dict[str, Any]:
    """سرهم‌کردن پاسخ کامل پیشخوان از تصویر خام."""
    from apps.waitlist.services import queue_stats  # واردکردن تنبل: پرهیز از چرخهٔ اپ‌ها

    user = authenticated_user(request)
    context = {"request": request, "now": snap.now, "rules": snap.rules}
    status = mfa.status_for(request._request)
    stats = queue_stats()
    return {
        "now": snap.now,
        "day": snap.day,
        "day_label": format_jalali(snap.day, with_weekday=True),
        "shift": snap.shift.as_dict(),
        "officer": {
            "display_name": user.display_name,
            "initials": _initials(user.display_name),
            "role": user.role,
            "role_label": UserRole(user.role).label if user.role in UserRole.values else "کارکنان",
            "is_manager": bool(user.is_manager),
        },
        "mfa": {
            "verified": status.verified,
            "method": status.method,
            "step_up_valid_until": status.step_up_valid_until,
            "step_up_seconds": status.step_up_seconds,
            "recovery_codes_remaining": status.recovery_codes_remaining,
        },
        "units": [unit_payload(card, context=context) for card in snap.cards],
        "kpis": desk.kpis(snap),
        "ledger": DeskRowSerializer(snap.ledger, many=True, context=context).data,
        "events": [event.as_dict() for event in snap.events],
        "waitlist": {
            "waiting": stats["waiting"],
            "offered": stats["offered"],
            "open_offers": stats["open_offers"],
            "expiring_soon": stats["expiring_soon"],
        },
        "rules": {
            "checkin_early_hours": snap.rules.early_hours,
            "no_show_grace_hours": snap.rules.grace_hours,
            "late_minutes": desk.LATE_MINUTES,
        },
        "housekeeping": {
            "presets": list(housekeeping.PRESET_MINUTES),
            "default_minutes": housekeeping.DEFAULT_MINUTES,
            "min_minutes": housekeeping.MIN_MINUTES,
            "max_minutes": housekeeping.MAX_MINUTES,
            "suggestions": housekeeping.assignee_suggestions(),
        },
        "block_kinds": [{"value": v, "label": str(lbl)} for v, lbl in BlockKind.choices],
        "cancel_reasons": [{"value": v, "label": str(lbl)} for v, lbl in CancelReasonKind.choices],
        "poll_seconds": desk.POLL_SECONDS,
    }


def _no_store(response: Response) -> Response:
    response["Cache-Control"] = "private, no-store"
    return response


# ── نماها ───────────────────────────────────────────────────────────────────


class DeskView(APIView):
    """تصویر زندهٔ پیشخوان — یک درخواست برای کل صفحه."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="تصویر زندهٔ میز پذیرش",
        description=(
            "وضعیت همین لحظهٔ ۵ واحد (مقیم/در انتظار/آماده/نظافت/مسدود)، جای کلیدها، "
            "شاخص‌ها، دفتر نوبت‌های امروز با پرچم‌های عملیاتی، رخدادنگار شیفت و خلاصهٔ صف."
        ),
        responses={200: DeskSerializer, 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:
        """ساخت و بازگرداندن تصویر."""
        return _no_store(Response(desk_payload(request, desk.snapshot())))


class DeskLookupView(APIView):
    """جست‌وجوی اسکنر/دستی پیشخوان."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="جست‌وجوی پیشخوان (QR، کد رزرو، کد آفلاین، کد ملی، نام/شماره)",
        parameters=[OpenApiParameter("q", str, required=True, description="ورودی اسکنر یا تایپ")],
        responses={200: DeskLookupSerializer, 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:
        """تشخیص نوع ورودی و یافتن رزروهای فعال نزدیک به امروز."""
        now = timezone.now()
        result = desk.lookup(str(request.query_params.get("q", "")), now=now)
        if result.kind == "national_id":
            user = authenticated_user(request)
            audit_record(
                AuditEntry(
                    action=AuditAction.SENSITIVE_DATA_READ,
                    actor_id=user.pk,
                    actor_label=user.display_name,
                    target_type="reservation",
                    target_id=",".join(r.code for r in result.matches)[:60],
                    reason="جست‌وجوی پیشخوان با کد ملی",
                    metadata={"matches": len(result.matches)},
                    request=request._request,
                )
            )
        context = {"request": request, "now": now, "rules": desk.DeskRules.load()}
        cert = result.certificate
        return _no_store(
            Response(
                {
                    "kind": result.kind,
                    "kind_label": LOOKUP_LABELS[result.kind],
                    "matches": DeskRowSerializer(result.matches, many=True, context=context).data,
                    "certificate": (
                        {
                            "valid": cert.valid,
                            "crypto_valid": cert.crypto_valid,
                            "mismatch": cert.mismatch,
                            "error": cert.error,
                        }
                        if cert is not None
                        else None
                    ),
                }
            )
        )


def _unit_or_404(code: str) -> Unit:
    unit = unit_by_code(code)
    if unit is None:
        raise NotFoundError("واحدی با این کد یافت نشد.")
    return unit


def unit_response(request: Request, code: str) -> Response:
    """کارت تازهٔ یک واحد پس از کنش (از همان قواعد تصویر کامل)."""
    snap = desk.snapshot()
    card = next((c for c in snap.cards if c.unit.code == code), None)
    if card is None:
        raise NotFoundError("واحدی با این کد یافت نشد.")
    context = {"request": request, "now": snap.now, "rules": snap.rules}
    return _no_store(Response({"unit": unit_payload(card, context=context)}))


class HousekeepingStartView(APIView):
    """سپردن دستی واحد به نظافت (بی‌تخلیه؛ مثلاً پس از عدم‌حضور یا جابه‌جایی)."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="آغاز نظافت واحد",
        request=HousekeepingStartSerializer,
        responses={
            200: DeskUnitResponseSerializer,
            403: PROBLEM_403,
            404: PROBLEM_404,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request, code: str) -> Response:
        """ثبت نوبت نظافت و بازگرداندن کارت تازه."""
        unit = _unit_or_404(code)
        serializer = HousekeepingStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        housekeeping.start(
            unit=unit,
            assignee=serializer.validated_data["assignee"],
            minutes=serializer.validated_data.get("minutes"),
            note=serializer.validated_data.get("note", ""),
            by=authenticated_user(request),
            request=request._request,
        )
        return unit_response(request, unit.code)


class HousekeepingCompleteView(APIView):
    """تأیید پایان نظافت ⇒ واحد آمادهٔ تحویل کلید."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="پایان نظافت واحد",
        request=None,
        responses={
            200: DeskUnitResponseSerializer,
            403: PROBLEM_403,
            404: PROBLEM_404,
            409: PROBLEM_409,
        },
    )
    def post(self, request: Request, code: str) -> Response:
        """بستن نوبت باز و بازگرداندن کارت تازه."""
        unit = _unit_or_404(code)
        with transaction.atomic():
            housekeeping.complete(
                unit=unit, by=authenticated_user(request), request=request._request
            )
        return unit_response(request, unit.code)
