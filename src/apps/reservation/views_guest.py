"""نماهای «تجربهٔ زائر» — هر آنچه فرانت برای یک سفر بی‌دغدغه لازم دارد.

    • ``me/standing``: «الان می‌توانم رزرو کنم؟» — پیش از نمایش دکمهٔ رزرو.
    • ``reservations/{code}/cancel-preview``: پیامد لغو، پیش از تأیید زائر.
    • ``reservations/{code}/timeline``: تاریخچهٔ رزرو (بدون جزئیات داخلی کارکنان).
    • ``reservations/{code}/calendar.ics`` و ``me/calendar.ics``: افزودن به تقویم گوشی.
    • ``reservations/{code}/certificate``: گواهی اقامت امضاشده با QR و کد آفلاین.
    • ``certificates/verify/{token}`` و ``certificates/verify-offline``: تأیید عمومی گواهی.
    • ``feedback/{token}``: نظرسنجی پس از اقامت با پیوند امضاشدهٔ پیامک (بدون ورود).

همهٔ نوشتن‌ها از سرویس‌ها می‌گذرد (تست معماری)، و همهٔ خروجی‌های حساس در
زنجیرهٔ حسابرسی ثبت می‌شوند — دقیقاً همان رفتار صفحه‌های SSR.
"""

from __future__ import annotations

from typing import Any, Final

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.errors import ConflictError, DomainError, GoneError, NotFoundError
from apps.core.schema import PROBLEM_401, PROBLEM_404, PROBLEM_409, PROBLEM_410, PROBLEM_422
from apps.identity.permissions import authenticated_user
from apps.reservation import feedback as feedback_services, ics, selectors, standing
from apps.reservation.certificate import (
    build_certificate,
    certificate_qr_svg,
    certificate_verify_url,
    verify_fields,
    verify_online,
)
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import GuestFeedback, Reservation
from apps.reservation.serializers import (
    CancelPreviewSerializer,
    ReservationEventListSerializer,
    ReservationEventSerializer,
    StandingSerializer,
)
from apps.reservation.views import reservation_for

PRIVATE_NO_STORE: Final[str] = "private, no-store"
ICS_RESPONSE: Final[dict[Any, Any]] = {
    (200, "text/calendar"): OpenApiResponse(
        response=OpenApiTypes.STR, description="پروندهٔ iCalendar (RFC 5545)"
    ),
    401: PROBLEM_401,
    404: PROBLEM_404,
}
CERTIFIABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN, ReservationStatus.COMPLETED}
)


def _ics_response(body: str, *, file_name: str) -> HttpResponse:
    """پاسخ دانلود تقویم با سرصفحه‌های درست (بدون کش مشترک)."""
    response = HttpResponse(body, content_type=ics.CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    response["Cache-Control"] = PRIVATE_NO_STORE
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _audit_calendar(request: Request, *, target_id: str, metadata: dict[str, Any]) -> None:
    """ثبت خروجی تقویم در حسابرسی (همان رفتار SSR)."""
    user = authenticated_user(request)
    audit_record(
        AuditEntry(
            action=AuditAction.CALENDAR_EXPORTED,
            actor_id=user.pk,
            actor_label=user.display_name,
            target_type="reservation",
            target_id=target_id,
            metadata=metadata,
            request=request._request,
        )
    )


class StandingView(APIView):
    """توان رزرو زائر در همین لحظه — سهمیه‌ها، محدودیت‌ها و موانع."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="وضعیت توان رزرو من",
        description=(
            "پیش از نمایش دکمهٔ «رزرو» بپرسید: سهمیهٔ فعال/دوره‌ای/سالانه، خنک‌شدن پس از "
            "لغو دیرهنگام، محرومیت عدم‌حضور و تکمیل پروفایل — با همان کدهای پیش‌بینی رزرو."
        ),
        responses={200: StandingSerializer, 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """محاسبهٔ وضعیت از روی همان قواعد موتور رزرو."""
        response = Response(standing.guest_standing(authenticated_user(request)).as_dict())
        response["Cache-Control"] = PRIVATE_NO_STORE
        return response


class CancelPreviewView(APIView):
    """پیامد لغو این رزرو در همین لحظه — برای دیالوگ تأیید لغو."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="پیش‌نمایش لغو رزرو",
        responses={200: CancelPreviewSerializer, 401: PROBLEM_401, 404: PROBLEM_404},
    )
    def get(self, request: Request, code: str) -> Response:
        """رایگان یا دیرهنگام؛ و اگر دیرهنگام، تا کی رزرو تازه بسته می‌ماند."""
        reservation = reservation_for(request, code)
        response = Response(standing.cancel_preview(reservation).as_dict())
        response["Cache-Control"] = PRIVATE_NO_STORE
        return response


class ReservationTimelineView(APIView):
    """تاریخچهٔ رزرو برای زائر — ثبت، تأیید، جابه‌جایی، پذیرش، تخلیه."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="تاریخچهٔ رزرو من",
        responses={200: ReservationEventListSerializer, 401: PROBLEM_401, 404: PROBLEM_404},
    )
    def get(self, request: Request, code: str) -> Response:
        """رخدادهای رزرو بدون نام کارمند، دلیل داخلی و یادداشت‌ها."""
        reservation = reservation_for(request, code)
        events = selectors.events_for(reservation).exclude(kind="note")
        return Response({"results": ReservationEventSerializer(events, many=True).data})


class ReservationIcsView(APIView):
    """پروندهٔ تقویم یک رزرو (با یادآور ۲ ساعت پیش از ورود)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["رزرو"], summary="افزودن رزرو به تقویم (ICS)", responses=ICS_RESPONSE)
    def get(self, request: Request, code: str) -> HttpResponse:
        """ساخت ICS برای رزرو صاحب درخواست (یا هر رزرو برای کارکنان)."""
        reservation = reservation_for(request, code)
        _audit_calendar(
            request,
            target_id=str(reservation.pk),
            metadata={"code": reservation.code, "scope": "single", "via": "api"},
        )
        return _ics_response(
            ics.reservation_ics(reservation), file_name=ics.calendar_file_name(reservation.code)
        )


class MyIcsView(APIView):
    """پروندهٔ تقویم همهٔ اقامت‌های پیش‌روی من."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["رزرو"], summary="تقویم همهٔ اقامت‌های پیش‌رو (ICS)", responses=ICS_RESPONSE)
    def get(self, request: Request) -> HttpResponse:
        """ICS رزروهای فعال آیندهٔ کاربر."""
        user = authenticated_user(request)
        reservations = [
            reservation
            for reservation in selectors.reservations_for_user(user, scope=selectors.UPCOMING)
            if reservation.status in ReservationStatus.active_statuses()
        ]
        _audit_calendar(
            request,
            target_id=str(user.pk),
            metadata={"scope": "mine", "count": len(reservations), "via": "api"},
        )
        return _ics_response(ics.ics_for(reservations), file_name="mahdieh-my-stays.ics")


class CertificateFieldSerializer(serializers.Serializer[Any]):
    """یک سطر گواهی: برچسب و مقدار."""

    label = serializers.CharField()  # type: ignore[assignment]
    value = serializers.CharField()


class CertificateSerializer(serializers.Serializer[Any]):
    """گواهی اقامت قابل‌چاپ (سند)."""

    code = serializers.CharField()
    holder = serializers.CharField()
    issued_on = serializers.CharField()
    short_code = serializers.CharField()
    verify_url = serializers.URLField()
    offline_check_url = serializers.URLField()
    qr_svg = serializers.CharField(help_text="SVG درون‌خطی (بدون اعلان XML)")
    fields = CertificateFieldSerializer(many=True)  # type: ignore[assignment]


def _base_url(request: Request) -> str:
    """نشانی پایهٔ درخواست — تا گواهی روی هر دامنهٔ استقرار درست کار کند."""
    return request.build_absolute_uri("/")


class ReservationCertificateView(APIView):
    """گواهی اقامت امضاشده — QR آنلاین + کد کوتاه آفلاین."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["رزرو"],
        summary="گواهی اقامت",
        description="فقط برای رزروهای تأییدشده، پذیرش‌شده یا پایان‌یافته (لغوشده ⇒ ۴۰۹).",
        responses={
            200: CertificateSerializer,
            401: PROBLEM_401,
            404: PROBLEM_404,
            409: PROBLEM_409,
        },
    )
    def get(self, request: Request, code: str) -> Response:
        """ساخت محتوای گواهی و QR برای چاپ یا ذخیره در گوشی."""
        reservation = reservation_for(request, code)
        if reservation.status not in CERTIFIABLE_STATUSES:
            raise ConflictError(
                f"برای رزرو در وضعیت «{reservation.status_label()}» گواهی صادر نمی‌شود.",
                code="certificate_unavailable",
            )
        base_url = _base_url(request)
        certificate = build_certificate(reservation)
        response = Response(
            {
                "code": certificate.code,
                "holder": certificate.holder,
                "issued_on": certificate.issued_on,
                "short_code": certificate.short_code,
                "verify_url": certificate_verify_url(certificate, base_url=base_url),
                "offline_check_url": request.build_absolute_uri("/verify/"),
                "qr_svg": certificate_qr_svg(certificate, base_url=base_url),
                "fields": [
                    {"label": label, "value": value} for label, value in certificate.fields()
                ],
            }
        )
        response["Cache-Control"] = PRIVATE_NO_STORE
        return response


class CertificateVerificationSerializer(serializers.Serializer[Any]):
    """نتیجهٔ تأیید آنلاین گواهی (سند)."""

    valid = serializers.BooleanField()
    crypto_valid = serializers.BooleanField()
    mismatch = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True)
    payload = serializers.DictField()
    reservation_status = serializers.CharField(allow_null=True)
    checked_at = serializers.DateTimeField()


class CertificateVerifyView(APIView):
    """تأیید عمومی گواهی از روی توکن QR — بدون ورود؛ هر بازدید حسابرسی می‌شود."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["گواهی"],
        summary="تأیید آنلاین گواهی اقامت",
        description=(
            "همیشه ۲۰۰ با ``valid`` برمی‌گرداند تا صفحهٔ فرانت هر سه حالت را روشن نشان دهد: "
            "معتبر، امضای نادرست/منقضی، یا ناهم‌خوان با سامانه."
        ),
        responses={200: CertificateVerificationSerializer},
    )
    def get(self, request: Request, token: str) -> Response:
        """اعتبارسنجی امضا و تطبیق با رکورد فعلی."""
        result = verify_online(token)
        audit_record(
            AuditEntry(
                action=AuditAction.SENSITIVE_DATA_READ,
                actor_label="تأیید گواهی",
                target_type="reservation",
                target_id=str(result.payload.get("code", "")),
                outcome="success" if result.valid else "denied",
                reason="تأیید آنلاین گواهی اقامت",
                metadata={"valid": result.crypto_valid, "mismatch": result.mismatch, "via": "api"},
                request=request._request,
            )
        )
        response = Response(result.as_dict())
        response["Cache-Control"] = PRIVATE_NO_STORE
        return response


class OfflineCheckSerializer(serializers.Serializer[Any]):
    """فیلدهای روی گواهی چاپی + کد کوتاه."""

    code = serializers.CharField(max_length=20)
    holder = serializers.CharField(max_length=120)
    unit = serializers.CharField(max_length=8)
    floor = serializers.IntegerField(min_value=0, max_value=20)
    guests = serializers.IntegerField(min_value=1, max_value=60)
    starts = serializers.CharField(max_length=80)
    ends = serializers.CharField(max_length=80)
    short_code = serializers.CharField(max_length=12)


class OfflineCheckResultSerializer(serializers.Serializer[Any]):
    """نتیجهٔ بازرسی آفلاین."""

    valid = serializers.BooleanField()


class CertificateOfflineCheckView(APIView):
    """بازمحاسبهٔ کد کوتاه گواهی از روی فیلدهای کاغذ — بدون دیتابیس."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["گواهی"],
        summary="بازرسی آفلاین گواهی",
        request=OfflineCheckSerializer,
        responses={200: OfflineCheckResultSerializer, 422: PROBLEM_422},
    )
    def post(self, request: Request) -> Response:
        """مقایسهٔ زمان-ثابت کد کوتاه بازمحاسبه‌شده با کد چاپی."""
        serializer = OfflineCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        valid = verify_fields(**serializer.validated_data)
        audit_record(
            AuditEntry(
                action=AuditAction.SENSITIVE_DATA_READ,
                actor_label="بازرسی آفلاین گواهی",
                target_type="reservation",
                target_id=str(serializer.validated_data["code"]).upper(),
                outcome="success" if valid else "denied",
                reason="بازرسی آفلاین گواهی اقامت",
                metadata={"valid": valid, "via": "api"},
                request=request._request,
            )
        )
        return Response({"valid": valid})


class FeedbackStateSerializer(serializers.Serializer[Any]):
    """وضعیت نظرسنجی یک اقامت (سند)."""

    state = serializers.ChoiceField(choices=["open", "submitted", "closed"])
    reason = serializers.CharField(allow_blank=True)
    reservation = serializers.DictField()
    rating_labels = serializers.DictField(child=serializers.CharField())
    subscores = serializers.ListField(child=serializers.DictField())
    comment_max_length = serializers.IntegerField()
    submitted = serializers.DictField(allow_null=True)


class FeedbackSubmitSerializer(serializers.Serializer[Any]):
    """بدنهٔ ثبت نظر — امتیاز کلی اجباری، بقیه اختیاری."""

    rating = serializers.IntegerField(
        min_value=feedback_services.MIN_RATING, max_value=feedback_services.MAX_RATING
    )
    comment = serializers.CharField(
        max_length=feedback_services.COMMENT_MAX_LENGTH, required=False, allow_blank=True
    )
    would_recommend = serializers.BooleanField(required=False, allow_null=True, default=None)
    scores = serializers.DictField(
        child=serializers.IntegerField(
            min_value=feedback_services.MIN_RATING,
            max_value=feedback_services.MAX_RATING,
            allow_null=True,
        ),
        required=False,
        help_text="زیرامتیازها: " + ", ".join(feedback_services.SUBSCORE_FIELDS),
    )


def _feedback_reservation(token: str) -> Reservation:
    """خواندن توکن نظرسنجی؛ پیوند کهنه/دست‌کاری‌شده ⇒ ۴۱۰، رزرو حذف‌شده ⇒ ۴۰۴."""
    try:
        return feedback_services.resolve_token(token)
    except NotFoundError:
        raise
    except DomainError as exc:
        raise GoneError(exc.detail, code="feedback_link_invalid") from exc


def _submitted_payload(feedback: GuestFeedback) -> dict[str, Any]:
    """خلاصهٔ پاسخ ثبت‌شده برای صفحهٔ «سپاس»."""
    return {
        "rating": feedback.rating,
        "would_recommend": feedback.would_recommend,
        "comment": feedback.comment,
    }


def _feedback_state(reservation: Reservation) -> dict[str, Any]:
    """وضعیت کامل فرم نظرسنجی برای فرانت."""
    existing = GuestFeedback.objects.filter(reservation=reservation).first()
    eligibility = feedback_services.eligibility(reservation)
    if existing is not None and existing.is_submitted:
        state, reason = "submitted", ""
    elif eligibility.eligible:
        state, reason = "open", ""
    else:
        state, reason = "closed", eligibility.reason
    return {
        "state": state,
        "reason": reason,
        "reservation": {
            "code": reservation.code,
            "unit_title": reservation.unit.title,
            "date_jalali": reservation.stay_date_jalali,
            "duration_label": reservation.duration_label,
        },
        "rating_labels": {
            str(key): value for key, value in feedback_services.RATING_LABELS.items()
        },
        "subscores": [
            {"field": field, "label": label} for field, label in feedback_services.SUBSCORE_LABELS
        ],
        "comment_max_length": feedback_services.COMMENT_MAX_LENGTH,
        "submitted": _submitted_payload(existing)
        if existing is not None and existing.is_submitted
        else None,
    }


class FeedbackTokenView(APIView):
    """نظرسنجی پس از اقامت با پیوند امضاشدهٔ پیامک — بدون ورود."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["نظرسنجی"],
        summary="وضعیت فرم نظرسنجی",
        operation_id="feedback_token_retrieve",
        responses={200: FeedbackStateSerializer, 404: PROBLEM_404, 410: PROBLEM_410},
    )
    def get(self, _request: Request, token: str) -> Response:
        """باز / ثبت‌شده / بسته (با دلیل) — به‌همراه برچسب‌های فرم."""
        response = Response(_feedback_state(_feedback_reservation(token)))
        response["Cache-Control"] = PRIVATE_NO_STORE
        return response

    @extend_schema(
        tags=["نظرسنجی"],
        summary="ثبت نظر زائر",
        operation_id="feedback_token_submit",
        request=FeedbackSubmitSerializer,
        responses={
            201: FeedbackStateSerializer,
            404: PROBLEM_404,
            409: PROBLEM_409,
            410: PROBLEM_410,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request, token: str) -> Response:
        """اعتبارسنجی و ثبت یک‌بارهٔ نظر؛ پاسخ دوم ⇒ ۴۰۹."""
        reservation = _feedback_reservation(token)
        serializer = FeedbackSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        existing = GuestFeedback.objects.filter(reservation=reservation).first()
        if existing is not None and existing.is_submitted:
            raise ConflictError(
                "نظر شما برای این اقامت پیش‌تر ثبت شده است. سپاس!", code="feedback_already_submitted"
            )
        scores = {
            field: (data.get("scores") or {}).get(field)
            for field in feedback_services.SUBSCORE_FIELDS
        }
        feedback_services.submit_feedback(
            token=token,
            rating=int(data["rating"]),
            comment=str(data.get("comment") or ""),
            would_recommend=data.get("would_recommend"),
            scores=scores,
            request=request._request,
        )
        return Response(_feedback_state(reservation), status=status.HTTP_201_CREATED)
