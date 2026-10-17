"""دفتر زائران برای میز پذیرش — جست‌وجو، کارت زائر و نمایش کنترل‌شدهٔ شماره.

قاعدهٔ حریم خصوصی: در فهرست و کارت، شماره همیشه **پوشانده** است. شمارهٔ کامل
فقط با درخواست صریح، با هدف مشخص، با تأیید تازهٔ عامل دوم و با ثبت در
حسابرسی نمایش داده می‌شود — دقیقاً مثل گاوصندوقی که هر بار بازکردنش امضا می‌خواهد.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import NotFoundError
from apps.core.pagination import StaffDirectoryCursorPagination
from apps.core.schema import PROBLEM_403, PROBLEM_404, PROBLEM_422
from apps.core.serializers import PageMetaSerializer
from apps.identity import services as identity_services
from apps.identity.choices import Province
from apps.identity.countries import country_title
from apps.identity.models import User
from apps.identity.permissions import HasFreshMfa, IsStaffRole, authenticated_user
from apps.identity.throttles import SensitiveActionThrottle
from apps.reservation import selectors, standing
from apps.reservation.serializers import StaffReservationSerializer, StandingSerializer

RECENT_RESERVATIONS: Final[int] = 20
QUERY_MAX_LENGTH: Final[int] = 64


class GuestRowSerializer(serializers.Serializer[Any]):
    """یک سطر دفتر زائران — بدون هیچ دادهٔ روشن حساس."""

    id = serializers.UUIDField()
    display_name = serializers.CharField()
    phone_masked = serializers.CharField(source="masked_phone")
    nationality = serializers.CharField()
    nationality_title = serializers.SerializerMethodField()
    province_title = serializers.SerializerMethodField()
    city = serializers.CharField()
    is_profile_complete = serializers.BooleanField()
    reservations_count = serializers.IntegerField()
    no_show_count = serializers.IntegerField()
    last_stay_end = serializers.DateTimeField(allow_null=True)
    date_joined = serializers.DateTimeField()

    def get_nationality_title(self, obj: User) -> str:
        """نام فارسی کشور."""
        return country_title(obj.nationality)

    def get_province_title(self, obj: User) -> str:
        """نام فارسی استان (برای زائر ایرانی)."""
        return Province.label_of(obj.province) if obj.province else ""


class GuestListSerializer(serializers.Serializer[Any]):
    """پوشش فهرست زائران (سند)."""

    results = GuestRowSerializer(many=True)
    meta = PageMetaSerializer()


class GuestDetailSerializer(serializers.Serializer[Any]):
    """کارت زائر: مشخصات پوشانده، وضعیت توان رزرو و رزروهای اخیر (سند)."""

    guest = GuestRowSerializer()
    national_id_masked = serializers.CharField()
    id_document_type = serializers.CharField()
    standing = StandingSerializer()
    recent_reservations = StaffReservationSerializer(many=True)


class RevealPhoneSerializer(serializers.Serializer[Any]):
    """درخواست نمایش شمارهٔ کامل — با هدف ساختاریافته."""

    purpose = serializers.ChoiceField(choices=list(identity_services.PHONE_REVEAL_PURPOSES))
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)


class RevealedPhoneSerializer(serializers.Serializer[Any]):
    """شمارهٔ کامل زائر (فقط در پاسخ همین درخواست؛ هرگز کش نشود)."""

    phone = serializers.CharField()


def _guest_or_404(guest_id: UUID) -> User:
    """یافتن زائر؛ کارمند یا شناسهٔ ناموجود ⇒ ۴۰۴."""
    guest = selectors.guest_by_id(guest_id)
    if guest is None:
        raise NotFoundError("زائری با این شناسه یافت نشد.")
    return guest


class StaffGuestListView(APIView):
    """دفتر زائران — جست‌وجو با بخشی از شماره یا نام."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="دفتر زائران",
        operation_id="staff_guests_list",
        parameters=[OpenApiParameter("q", str, description="بخشی از شماره یا نام")],
        responses={200: GuestListSerializer, 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:
        """فهرست صفحه‌بندی‌شدهٔ زائران با شمارش‌های خلاصه."""
        queryset = selectors.guest_directory(request.query_params.get("q", "")[:QUERY_MAX_LENGTH])
        paginator = StaffDirectoryCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        return paginator.get_paginated_response(GuestRowSerializer(page, many=True).data)


class StaffGuestDetailView(APIView):
    """کارت زائر برای پذیرش."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["کارکنان"],
        summary="کارت زائر",
        operation_id="staff_guests_retrieve",
        responses={200: GuestDetailSerializer, 403: PROBLEM_403, 404: PROBLEM_404},
    )
    def get(self, _request: Request, guest_id: UUID) -> Response:
        """مشخصات پوشانده + توان رزرو + آخرین رزروها."""
        guest = _guest_or_404(guest_id)
        recent = selectors.reservations_for_user(guest, scope=selectors.ALL)[:RECENT_RESERVATIONS]
        return Response(
            {
                "guest": GuestRowSerializer(guest).data,
                "national_id_masked": guest.masked_national_id,
                "id_document_type": guest.id_document_type,
                "standing": standing.guest_standing(guest).as_dict(),
                "recent_reservations": StaffReservationSerializer(recent, many=True).data,
            }
        )


class StaffGuestRevealPhoneView(APIView):
    """نمایش شمارهٔ کامل زائر — تأیید تازهٔ عامل دوم + هدف + حسابرسی."""

    permission_classes = [IsStaffRole, HasFreshMfa]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["کارکنان"],
        summary="نمایش شمارهٔ کامل زائر",
        description=(
            "اگر آخرین تأیید عامل دوم کهنه باشد ⇒ ۴۰۳ با کد ``step_up_required``؛ "
            "کلاینت کد تازه می‌گیرد (``auth/mfa/verify``) و دوباره می‌فرستد."
        ),
        request=RevealPhoneSerializer,
        responses={
            200: RevealedPhoneSerializer,
            403: PROBLEM_403,
            404: PROBLEM_404,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request, guest_id: UUID) -> Response:
        """بازگرداندن شمارهٔ کامل و ثبت رویداد در حسابرسی."""
        guest = _guest_or_404(guest_id)
        serializer = RevealPhoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = identity_services.reveal_guest_phone(
            guest=guest,
            by=authenticated_user(request),
            purpose=serializer.validated_data["purpose"],
            note=serializer.validated_data.get("note", ""),
            request=request._request,
        )
        response = Response({"phone": phone})
        response["Cache-Control"] = "private, no-store"
        return response
