"""نماهای HTTP رزرو — تقویم عمومی، مسیر زائر و مسیر کارکنان.

قواعد این لایه:
    • هیچ نوشتنی مستقیم روی مدل — همه از سرویس‌ها (تست معماری آن را اجرا می‌کند).
    • هر نوشتن زائر با هدر ``Idempotency-Key`` هم‌توان می‌شود.
    • خطاها همیشه ``application/problem+json`` (RFC 9457).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError as DomainValidationError,
)
from apps.core.idempotency import IDEMPOTENCY_HEADER, execute_idempotent
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_404, PROBLEM_409, PROBLEM_422
from apps.identity.permissions import IsProfileComplete, IsStaffRole, authenticated_user
from apps.identity.throttles import SensitiveActionThrottle
from apps.inventory import selectors as inventory_selectors
from apps.reservation import availability, selectors, services
from apps.reservation.models import Reservation
from apps.reservation.policy import policy_int
from apps.reservation.serializers import (
    CalendarSerializer,
    CancelSerializer,
    MoveSerializer,
    NoteSerializer,
    QuoteRequestSerializer,
    ReservationCreateSerializer,
    ReservationSerializer,
    StaffReservationCreateSerializer,
    StaffReservationSerializer,
    calendar_payload,
)

CALENDAR_DEFAULT_DAYS = 14
CALENDAR_MAX_DAYS = availability.MAX_MATRIX_DAYS


def _require_unit(code: str) -> Any:
    """یافتن واحد با کد یا خطای ۴۰۴ قابل‌فهم."""
    unit = inventory_selectors.unit_by_code(code)
    if unit is None:
        raise NotFoundError("واحد انتخاب‌شده وجود ندارد یا غیرفعال شده است.")
    return unit


def _reservation_for(request: Request, code: str) -> Reservation:
    """یافتن رزرو با کد + کنترل مالکیت (کارکنان همه را می‌بینند)."""
    reservation = selectors.reservation_by_code(code)
    if reservation is None:
        raise NotFoundError("رزروی با این کد یافت نشد.")
    user = request.user
    if reservation.guest_id != getattr(user, "id", None) and not getattr(
        user, "is_staff_member", False
    ):
        raise PermissionDeniedError("این رزرو به حساب شما تعلق ندارد.")
    return reservation


class CalendarView(APIView):
    """تقویم موجودی «واحد × نیم‌روز» — عمومی و قابل‌کش."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["تقویم"],
        summary="تقویم موجودی",
        description=(
            "برای هر روز، وضعیت هر نیم‌روز هر واحد را برمی‌گرداند: "
            "آزاد / اشغال / بافر نظافت / غیرقابل‌رزرو / گذشته."
        ),
        parameters=[
            OpenApiParameter("from", str, description="آغاز تقویم (YYYY-MM-DD)؛ پیش‌فرض امروز"),
            OpenApiParameter("days", int, description=f"تعداد روز (۱ تا {CALENDAR_MAX_DAYS})"),
        ],
        responses={200: CalendarSerializer, 422: PROBLEM_422},
    )
    def get(self, request: Request) -> Response:
        """ساخت ماتریس موجودی برای بازهٔ درخواستی."""
        raw_from = request.query_params.get("from")
        try:
            start_day = date.fromisoformat(raw_from) if raw_from else timezone.localdate()
        except ValueError as exc:
            raise DomainValidationError(
                "قالب تاریخ درست نیست؛ قالب مورد انتظار YYYY-MM-DD است.", code="invalid_date"
            ) from exc
        raw_days = request.query_params.get("days")
        try:
            days = int(raw_days) if raw_days else CALENDAR_DEFAULT_DAYS
        except ValueError as exc:
            raise DomainValidationError("پارامتر days باید عدد باشد.", code="invalid_days") from exc
        if days < 1 or days > CALENDAR_MAX_DAYS:
            raise DomainValidationError(
                f"تعداد روزها باید بین ۱ و {CALENDAR_MAX_DAYS} باشد.", code="invalid_days"
            )

        staff = bool(getattr(request.user, "is_staff_member", False))
        matrix = availability.availability_matrix(start_day=start_day, days=days, staff=staff)
        policy_values = {
            "max_cells": policy_int("reservation.max_cells"),
            "min_cells": policy_int("reservation.min_cells"),
            "horizon_days": policy_int("reservation.booking_horizon_days"),
            "cell_hours": policy_int("lattice.cell_hours"),
            "grid_start_hour": policy_int("lattice.grid_start_local"),
        }
        return Response(calendar_payload(matrix, policy_values=policy_values))


class QuoteView(APIView):
    """پیش‌بینی رزرو — «قبل از تأیید، همه‌چیز روشن»."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="پیش‌بینی رزرو (سبد)",
        description="همهٔ دلایل رد + بازه‌های جایگزین را **پیش از** ثبت رزرو برمی‌گرداند.",
        request=QuoteRequestSerializer,
        responses={200: dict, 401: PROBLEM_401, 422: PROBLEM_422},
    )
    def post(self, request: Request) -> Response:
        """محاسبهٔ نقل‌قول کامل برای یک درخواست رزرو."""
        serializer = QuoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        unit = _require_unit(data["unit_code"])
        quote = services.build_quote(
            unit=unit,
            start=data["start"],
            end=data["end"],
            guests_count=data["guests_count"],
            guest=authenticated_user(request),
        )
        return Response(quote.as_dict())


class ReservationListCreateView(APIView):
    """فهرست رزروهای من و ثبت رزرو تازه."""

    def get_permissions(self) -> list[Any]:
        """فهرست ⇒ فقط ورود؛ ثبت ⇒ ورود + پروفایل کامل."""
        if self.request.method == "POST":
            return [IsAuthenticated(), IsProfileComplete()]
        return [IsAuthenticated()]

    @extend_schema(
        tags=["رزرو"],
        summary="رزروهای من",
        parameters=[
            OpenApiParameter("scope", str, description="upcoming | past | all (پیش‌فرض upcoming)"),
        ],
        responses={200: ReservationSerializer(many=True), 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن رزروهای کاربر جاری در دامنهٔ درخواستی."""
        scope = request.query_params.get("scope", selectors.UPCOMING)
        if scope not in {selectors.UPCOMING, selectors.PAST, selectors.ALL}:
            scope = selectors.UPCOMING
        queryset = selectors.reservations_for_user(authenticated_user(request), scope=scope)
        return Response(ReservationSerializer(queryset[:100], many=True).data)

    @extend_schema(
        tags=["رزرو"],
        summary="ثبت رزرو",
        description=(
            "با ارسال هدر ``Idempotency-Key``، ارسال دوبارهٔ همان درخواست (ضربهٔ دوبارهٔ "
            "کاربر یا تلاش مجدد موبایل) رزرو دوم نمی‌سازد؛ همان پاسخ قبلی بازپخش می‌شود."
        ),
        parameters=[
            OpenApiParameter(
                IDEMPOTENCY_HEADER,
                str,
                location=OpenApiParameter.HEADER,
                description="کلید یکتای کلاینت برای جلوگیری از رزرو تکراری",
            )
        ],
        request=ReservationCreateSerializer,
        responses={
            201: ReservationSerializer,
            401: PROBLEM_401,
            403: PROBLEM_403,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request) -> Response:
        """ثبت رزرو از سمت زائر با تضمین هم‌توانی."""
        serializer = ReservationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        unit = _require_unit(data["unit_code"])

        def action() -> tuple[int, Any]:
            """اجرای واقعی ثبت رزرو — تنها در نبود نتیجهٔ ذخیره‌شده اجرا می‌شود."""
            reservation = services.create_reservation(
                guest=authenticated_user(request),
                unit=unit,
                start=data["start"],
                end=data["end"],
                guests_count=data["guests_count"],
                guest_note=data.get("guest_note", ""),
                expected_arrival=data.get("expected_arrival"),
                vehicle_plate=data.get("vehicle_plate", ""),
                request=request._request,
            )
            return status.HTTP_201_CREATED, ReservationSerializer(reservation).data

        key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
        if not key:
            result = action()
            return Response(result[1], status=result[0])
        outcome = execute_idempotent(
            key=key,
            scope="reservation.create",
            actor_ref=str(authenticated_user(request).pk),
            payload={
                "unit": unit.code,
                "start": data["start"].isoformat(),
                "end": data["end"].isoformat(),
                "guests": data["guests_count"],
            },
            action=action,
        )
        return Response(outcome.body, status=outcome.status)


class ReservationDetailView(APIView):
    """جزئیات یک رزرو با کد."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="جزئیات رزرو",
        responses={200: ReservationSerializer, 401: PROBLEM_401, 404: PROBLEM_404},
    )
    def get(self, request: Request, code: str) -> Response:
        """بازگرداندن یک رزرو با کنترل مالکیت."""
        reservation = _reservation_for(request, code)
        serializer_class = (
            StaffReservationSerializer
            if getattr(request.user, "is_staff_member", False)
            else ReservationSerializer
        )
        return Response(serializer_class(reservation).data)


class ReservationCancelView(APIView):
    """لغو رزرو — سانس بلافاصله آزاد می‌شود."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="لغو رزرو",
        request=CancelSerializer,
        responses={
            200: ReservationSerializer,
            401: PROBLEM_401,
            404: PROBLEM_404,
            409: PROBLEM_409,
        },
    )
    def post(self, request: Request, code: str) -> Response:
        """لغو رزرو کاربر (یا توسط کارکنان با دلیل اجباری)."""
        reservation = _reservation_for(request, code)
        serializer = CancelSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        is_staff = bool(getattr(actor, "is_staff_member", False)) and (
            reservation.guest_id != actor.pk
        )
        updated = services.cancel_reservation(
            reservation=reservation,
            by=authenticated_user(request),
            reason_kind=serializer.validated_data.get("reason_kind", "guest_request"),
            reason=serializer.validated_data.get("reason", ""),
            is_staff=is_staff,
            request=request._request,
        )
        serializer_class = StaffReservationSerializer if is_staff else ReservationSerializer
        return Response(serializer_class(updated).data)


class StaffReservationListView(APIView):
    """تابلوی کارکنان — رزروهای یک بازه."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="تابلوی رزروهای بازه",
        parameters=[
            OpenApiParameter("from", str, description="آغاز بازه (YYYY-MM-DD)"),
            OpenApiParameter("days", int, description="تعداد روز (پیش‌فرض ۷)"),
        ],
        responses={200: StaffReservationSerializer(many=True), 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن رزروهای بازه برای پذیرش."""
        raw_from = request.query_params.get("from")
        try:
            start_day = date.fromisoformat(raw_from) if raw_from else timezone.localdate()
        except ValueError as exc:
            raise DomainValidationError("قالب تاریخ درست نیست.", code="invalid_date") from exc
        raw_days = request.query_params.get("days")
        days = int(raw_days) if raw_days and raw_days.isdigit() else 7
        start, end = (
            inventory_selectors.day_window(start_day)[0],
            inventory_selectors.day_window(start_day + timedelta(days=max(1, days)))[1],
        )
        queryset = selectors.staff_board(start=start, end=end)
        return Response(StaffReservationSerializer(queryset, many=True).data)


class StaffReservationDetailView(APIView):
    """جزئیات یک رزرو برای کارکنان — با هویت پوشاندهٔ زائر و زنجیرهٔ جانشینی."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="جزئیات رزرو برای کارکنان",
        responses={200: StaffReservationSerializer, 403: PROBLEM_403, 404: PROBLEM_404},
    )
    def get(self, _request: Request, code: str) -> Response:
        """بازگرداندن یک رزرو با کد — بدون کنترل مالکیت (کارمند مجاز است)."""
        reservation = selectors.reservation_by_code(code)
        if reservation is None:
            raise NotFoundError("رزروی با این کد یافت نشد.")
        return Response(StaffReservationSerializer(reservation).data)


class StaffReservationCreateView(APIView):
    """ثبت رزرو تلفنی/حضوری برای زائر — با جست‌وجوی سریع شماره."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="ثبت رزرو تلفنی/حضوری",
        request=StaffReservationCreateSerializer,
        responses={
            201: ReservationSerializer,
            403: PROBLEM_403,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request) -> Response:
        """ساخت حساب در صورت نبود، سپس ثبت رزرو با منبع تلفنی/حضوری."""
        serializer = StaffReservationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        unit = _require_unit(data["unit_code"])
        reservation = services.staff_create_reservation(
            phone=data["phone"],
            unit=unit,
            start=data["start"],
            end=data["end"],
            guests_count=data["guests_count"],
            by=authenticated_user(request),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            province=data.get("province", ""),
            city=data.get("city", ""),
            guest_note=data.get("guest_note", ""),
            staff_note=data.get("staff_note", ""),
            request=request._request,
        )
        return Response(ReservationSerializer(reservation).data, status=status.HTTP_201_CREATED)


class ReservationMoveView(APIView):
    """جابه‌جایی رزرو (سناریوی S4) — با کنترل نسخه و دلیل اجباری."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="جابه‌جایی رزرو",
        request=MoveSerializer,
        responses={
            200: ReservationSerializer,
            403: PROBLEM_403,
            404: PROBLEM_404,
            409: PROBLEM_409,
        },
    )
    def post(self, request: Request, code: str) -> Response:
        """جانشین‌کردن رزرو فعلی با رزرو تازه روی واحد/بازهٔ دیگر."""
        reservation = selectors.reservation_by_code(code)
        if reservation is None:
            raise NotFoundError("رزروی با این کد یافت نشد.")
        serializer = MoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        unit = _require_unit(data["unit_code"])
        replacement = services.move_reservation(
            reservation=reservation,
            unit=unit,
            start=data["start"],
            end=data["end"],
            by=authenticated_user(request),
            reason=data["reason"],
            expected_version=data.get("expected_version"),
            request=request._request,
        )
        return Response(ReservationSerializer(replacement).data)


class ReservationTransitionView(APIView):
    """انتقال‌های کارمندی: پذیرش، تخلیه، عدم‌حضور، یادداشت."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="پذیرش مهمان",
        request=None,
        responses={200: ReservationSerializer, 403: PROBLEM_403, 409: PROBLEM_409},
    )
    def post(self, request: Request, code: str, action_name: str) -> Response:
        """اجرای انتقال درخواستی روی رزرو."""
        reservation = selectors.reservation_by_code(code)
        if reservation is None:
            raise NotFoundError("رزروی با این کد یافت نشد.")

        if action_name == "check-in":
            updated = services.check_in(
                reservation=reservation,
                by=authenticated_user(request),
                request=request._request,
            )
        elif action_name == "check-out":
            updated = services.check_out(
                reservation=reservation,
                by=authenticated_user(request),
                request=request._request,
            )
        elif action_name == "no-show":
            serializer = NoteSerializer(data=request.data or {})
            serializer.is_valid(raise_exception=True)
            updated = services.mark_no_show(
                reservation=reservation,
                by=authenticated_user(request),
                reason=serializer.validated_data["staff_note"],
                request=request._request,
            )
        elif action_name == "note":
            serializer = NoteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            updated = services.update_staff_note(
                reservation=reservation,
                by=authenticated_user(request),
                note=serializer.validated_data["staff_note"],
                request=request._request,
            )
        else:
            raise NotFoundError("عملیات درخواستی وجود ندارد.")
        return Response(StaffReservationSerializer(updated).data)
