"""نماهای HTTP رزرو — تقویم عمومی، مسیر زائر و مسیر کارکنان.

قواعد این لایه:
    • هیچ نوشتنی مستقیم روی مدل — همه از سرویس‌ها (تست معماری آن را اجرا می‌کند).
    • هر نوشتن زائر با هدر ``Idempotency-Key`` هم‌توان می‌شود.
    • خطاها همیشه ``application/problem+json`` (RFC 9457).
"""

from __future__ import annotations

from datetime import date, timedelta
import time
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache import get_epoch
from apps.core.conditional import PRIVATE_REVALIDATE, conditional_response, weak_etag
from apps.core.errors import NotFoundError, ValidationError as DomainValidationError
from apps.core.idempotency import IDEMPOTENCY_HEADER, execute_idempotent
from apps.core.pagination import OwnerCursorPagination, StaffBoardCursorPagination
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_404, PROBLEM_409, PROBLEM_422
from apps.identity import mfa
from apps.identity.permissions import IsProfileComplete, IsStaffRole, authenticated_user
from apps.identity.throttles import SensitiveActionThrottle
from apps.inventory import selectors as inventory_selectors
from apps.reservation import arrival, availability, selectors, services
from apps.reservation.choices import ReservationSource, ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import POLICY_NAMESPACE, POLICY_SCOPE, policy_int
from apps.reservation.serializers import (
    TRANSITION_ACTIONS,
    CalendarSerializer,
    CancelSerializer,
    MoveSerializer,
    NoteSerializer,
    QuoteRequestSerializer,
    QuoteSerializer,
    ReservationCreateSerializer,
    ReservationDetailsUpdateSerializer,
    ReservationListSerializer,
    ReservationSerializer,
    StaffReservationCreateSerializer,
    StaffReservationEventListSerializer,
    StaffReservationEventSerializer,
    StaffReservationListSerializer,
    StaffReservationSerializer,
    TransitionRequestSerializer,
    calendar_payload,
)

CALENDAR_DEFAULT_DAYS = 14
CALENDAR_MAX_DAYS = availability.MAX_MATRIX_DAYS
# «گذشته» شدن خانه‌ها به ساعت بستگی دارد ⇒ ETag تقویم هر ساعت عوض می‌شود.
CALENDAR_ETAG_BUCKET_SECONDS = 3600
BOARD_DEFAULT_DAYS = 7
BOARD_MAX_DAYS = 62


def _csv(request: Request, name: str, allowed: set[str] | None = None) -> tuple[str, ...]:
    """خواندن پارامتر چندمقداری «a,b,c» (یا تکرار پارامتر) با پالایش مقادیر مجاز."""
    values: list[str] = []
    for raw in request.query_params.getlist(name):
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    if allowed is not None:
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise DomainValidationError(
                f"مقدار ناشناخته برای {name}: {', '.join(unknown)}",
                code="invalid_filter",
                errors={name: [f"مقادیر مجاز: {', '.join(sorted(allowed))}"]},
            )
    return tuple(dict.fromkeys(values))


def _require_unit(code: str) -> Any:
    """یافتن واحد با کد یا خطای ۴۰۴ قابل‌فهم."""
    unit = inventory_selectors.unit_by_code(code)
    if unit is None:
        raise NotFoundError("واحد انتخاب‌شده وجود ندارد یا غیرفعال شده است.")
    return unit


def staff_powers(request: Request) -> bool:
    """آیا درخواست‌کننده کارمندِ گذشته از سد عامل دوم است؟ (نماهای مشترک)."""
    return mfa.has_staff_powers(request._request)


def reservation_for(request: Request, code: str) -> Reservation:
    """یافتن رزرو با کد + کنترل مالکیت (کارکنان تأییدشده همه را می‌بینند).

    «وجود ندارد» و «مال شما نیست» عمداً **یک** پاسخ ۴۰۴ می‌گیرند تا کسی نتواند
    با حدس کدهای پیاپی (M-1405-0001، 0002 …) وجود رزرو دیگران را بفهمد.
    """
    reservation = selectors.reservation_by_code(code)
    if reservation is None or (
        reservation.guest_id != getattr(request.user, "id", None) and not staff_powers(request)
    ):
        raise NotFoundError("رزروی با این کد برای شما یافت نشد.")
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
            OpenApiParameter(
                "If-None-Match",
                str,
                location=OpenApiParameter.HEADER,
                description="ETag پاسخ قبلی؛ اگر تقویم تغییر نکرده باشد ⇒ ۳۰۴ بدون بدنه",
            ),
        ],
        responses={200: CalendarSerializer, 304: None, 422: PROBLEM_422},
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

        staff = staff_powers(request)
        scope = availability.SCOPE_STAFF if staff else availability.SCOPE_PUBLIC
        etag = weak_etag(
            "calendar",
            scope,
            get_epoch(availability.AVAILABILITY_NAMESPACE, scope),
            get_epoch(POLICY_NAMESPACE, POLICY_SCOPE),
            start_day.isoformat(),
            days,
            int(time.time() // CALENDAR_ETAG_BUCKET_SECONDS),
        )

        def build() -> dict[str, Any]:
            """ساخت ماتریس — فقط وقتی کلاینت نسخهٔ تازه را ندارد."""
            matrix = availability.availability_matrix(start_day=start_day, days=days, staff=staff)
            policy_values = {
                "max_cells": policy_int("reservation.max_cells"),
                "min_cells": policy_int("reservation.min_cells"),
                "horizon_days": policy_int("reservation.booking_horizon_days"),
                "cell_hours": policy_int("lattice.cell_hours"),
                "grid_start_hour": policy_int("lattice.grid_start_local"),
            }
            return calendar_payload(matrix, policy_values=policy_values)

        # نمای کارکنان جزئیات بیشتری دارد ⇒ نباید در کش مشترک بنشیند.
        return conditional_response(
            request,
            etag=etag,
            build=build,
            cache_control=PRIVATE_REVALIDATE if staff else "public, max-age=0, must-revalidate",
        )


class QuoteView(APIView):
    """پیش‌بینی رزرو — «قبل از تأیید، همه‌چیز روشن»."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="پیش‌بینی رزرو (سبد)",
        description="همهٔ دلایل رد + بازه‌های جایگزین را **پیش از** ثبت رزرو برمی‌گرداند.",
        request=QuoteRequestSerializer,
        responses={200: QuoteSerializer, 401: PROBLEM_401, 422: PROBLEM_422},
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
        # شناسهٔ صریح: با پوشش ``results`` دیگر «فهرست» از مسیر حدس زده نمی‌شود و
        # بدون این، شناسه با «جزئیات رزرو» تصادم می‌کرد (کلاینت فرانت می‌شکند).
        operation_id="reservations_list",
        parameters=[
            OpenApiParameter("scope", str, description="upcoming | past | all (پیش‌فرض upcoming)"),
        ],
        responses={200: ReservationListSerializer, 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن رزروهای کاربر جاری در دامنهٔ درخواستی (مکان‌نما + شمارش)."""
        scope = request.query_params.get("scope", selectors.UPCOMING)
        if scope not in {selectors.UPCOMING, selectors.PAST, selectors.ALL}:
            scope = selectors.UPCOMING
        queryset = selectors.reservations_for_user(authenticated_user(request), scope=scope)
        paginator = OwnerCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        return paginator.get_paginated_response(ReservationSerializer(page, many=True).data)

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
        operation_id="reservations_retrieve",
        responses={200: ReservationSerializer, 401: PROBLEM_401, 404: PROBLEM_404},
    )
    def get(self, request: Request, code: str) -> Response:
        """بازگرداندن یک رزرو با کنترل مالکیت."""
        reservation = reservation_for(request, code)
        serializer_class = (
            StaffReservationSerializer if staff_powers(request) else ReservationSerializer
        )
        return Response(serializer_class(reservation).data)

    @extend_schema(
        tags=["رزرو"],
        summary="هماهنگی ورود (ساعت رسیدن، پلاک، یادداشت)",
        description=(
            "زائر پس از ثبت، ساعت تقریبی رسیدن، پلاک خودرو و یادداشت برای پذیرش را به‌روز "
            "می‌کند — فقط پیش از پذیرش حضوری (``details_editable``). اثری بر سانس و سهمیه "
            "ندارد؛ در تاریخچه رخداد «هماهنگی ورود» ثبت می‌شود. پس از پذیرش/لغو ⇒ ۴۰۹ "
            "``details_locked``."
        ),
        operation_id="reservations_partial_update",
        request=ReservationDetailsUpdateSerializer,
        responses={
            200: ReservationSerializer,
            401: PROBLEM_401,
            404: PROBLEM_404,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
    )
    def patch(self, request: Request, code: str) -> Response:
        """به‌روزرسانی جزئی هماهنگی ورود رزرو خود زائر."""
        reservation = reservation_for(request, code)
        serializer = ReservationDetailsUpdateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        is_staff = staff_powers(request) and reservation.guest_id != actor.pk
        updated = arrival.update_arrival_details(
            reservation=reservation,
            by=actor,
            changes=dict(serializer.validated_data),
            is_staff=is_staff,
            request=request._request,
        )
        serializer_class = StaffReservationSerializer if is_staff else ReservationSerializer
        return Response(serializer_class(updated).data)


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
        reservation = reservation_for(request, code)
        serializer = CancelSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        is_staff = staff_powers(request) and reservation.guest_id != actor.pk
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
        operation_id="staff_reservations_list",
        parameters=[
            OpenApiParameter("from", str, description="آغاز بازه (YYYY-MM-DD)"),
            OpenApiParameter(
                "days", int, description=f"تعداد روز (پیش‌فرض ۷، سقف {BOARD_MAX_DAYS})"
            ),
            OpenApiParameter("q", str, description="جست‌وجو: کد رزرو، بخشی از شماره یا نام زائر"),
            OpenApiParameter(
                "status",
                str,
                description="وضعیت‌ها با ویرگول: "
                + ", ".join(choice.value for choice in ReservationStatus),
            ),
            OpenApiParameter("unit", str, description="کد واحدها با ویرگول (مثل A1,B3)"),
            OpenApiParameter(
                "source",
                str,
                description="منبع‌ها با ویرگول: "
                + ", ".join(choice.value for choice in ReservationSource),
            ),
        ],
        responses={200: StaffReservationListSerializer, 403: PROBLEM_403, 422: PROBLEM_422},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن رزروهای بازه برای پذیرش (صفحه‌بندی مکان‌نما)."""
        raw_from = request.query_params.get("from")
        try:
            start_day = date.fromisoformat(raw_from) if raw_from else timezone.localdate()
        except ValueError as exc:
            raise DomainValidationError("قالب تاریخ درست نیست.", code="invalid_date") from exc
        raw_days = request.query_params.get("days")
        days = int(raw_days) if raw_days and raw_days.isdigit() else BOARD_DEFAULT_DAYS
        days = min(max(1, days), BOARD_MAX_DAYS)
        # بازهٔ [from, from+days) — پیش‌تر یک روز اضافه هم می‌آمد (خطای مرز).
        start = inventory_selectors.day_window(start_day)[0]
        end = inventory_selectors.day_window(start_day + timedelta(days=days - 1))[1]
        filters = selectors.BoardFilter(
            query=request.query_params.get("q", "")[:64],
            statuses=_csv(request, "status", {choice.value for choice in ReservationStatus}),
            unit_codes=tuple(code.upper() for code in _csv(request, "unit")),
            sources=_csv(request, "source", {choice.value for choice in ReservationSource}),
        )
        queryset = selectors.staff_board(start=start, end=end, filters=filters)
        paginator = StaffBoardCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        return paginator.get_paginated_response(StaffReservationSerializer(page, many=True).data)


class StaffReservationDetailView(APIView):
    """جزئیات یک رزرو برای کارکنان — با هویت پوشاندهٔ زائر و زنجیرهٔ جانشینی."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="جزئیات رزرو برای کارکنان",
        operation_id="staff_reservations_retrieve",
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
            201: StaffReservationSerializer,
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
        return Response(
            StaffReservationSerializer(reservation).data, status=status.HTTP_201_CREATED
        )


class ReservationMoveView(APIView):
    """جابه‌جایی رزرو (سناریوی S4) — با کنترل نسخه و دلیل اجباری."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="جابه‌جایی رزرو",
        request=MoveSerializer,
        responses={
            200: StaffReservationSerializer,
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
        return Response(StaffReservationSerializer(replacement).data)


class ReservationTransitionView(APIView):
    """انتقال‌های کارمندی: پذیرش، تخلیه، عدم‌حضور، یادداشت."""

    permission_classes = [IsStaffRole]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="انتقال کارمندی رزرو (پذیرش / تخلیه / عدم‌حضور / یادداشت)",
        description=(
            "check-in: پذیرش (از چند ساعت پیش از آغاز اقامت باز است)؛ check-out: تخلیه؛ "
            "no-show: عدم‌حضور با یادداشت اجباری؛ note: ثبت یادداشت داخلی."
        ),
        parameters=[
            OpenApiParameter(
                "action_name", str, location=OpenApiParameter.PATH, enum=list(TRANSITION_ACTIONS)
            )
        ],
        request=TransitionRequestSerializer,
        responses={
            200: StaffReservationSerializer,
            403: PROBLEM_403,
            404: PROBLEM_404,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
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


class StaffReservationEventsView(APIView):
    """تاریخچهٔ کامل یک رزرو برای کارکنان — چه کسی، چه وقت، چرا."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="تاریخچهٔ رخدادهای رزرو",
        responses={200: StaffReservationEventListSerializer, 403: PROBLEM_403, 404: PROBLEM_404},
    )
    def get(self, _request: Request, code: str) -> Response:
        """رخدادهای رزرو به ترتیب زمان، با عامل و دلیل."""
        reservation = selectors.reservation_by_code(code)
        if reservation is None:
            raise NotFoundError("رزروی با این کد یافت نشد.")
        events = selectors.events_for(reservation)
        return Response({"results": StaffReservationEventSerializer(events, many=True).data})
