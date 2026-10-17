"""نماهای HTTP هویت — نازک، بدون منطق کسب‌وکار.

قاعدهٔ لایه‌بندی (که تست معماری آن را اجرا می‌کند): نمای HTTP هرگز مستقیم روی
مدل نمی‌نویسد؛ فقط سریال‌ایز می‌کند، سرویس را صدا می‌زند و پاسخ می‌سازد.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import login as auth_login, logout as auth_logout
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.schema import PROBLEM_401, PROBLEM_422, PROBLEM_429
from apps.identity import services
from apps.identity.permissions import authenticated_user
from apps.identity.serializers import (
    OtpRequestResultSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    ProfileSerializer,
    ProfileUpdateSerializer,
    SessionResultSerializer,
)
from apps.identity.throttles import OtpRequestThrottle, OtpVerifyThrottle

SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"


class OtpRequestView(APIView):
    """درخواست کد ورود با پیامک."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_classes = [OtpRequestThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="درخواست کد یک‌بارمصرف",
        description=(
            "کد یک‌بارمصرف را به شمارهٔ داده‌شده پیامک می‌کند. پاسخ برای شماره‌های "
            "ثبت‌شده و ثبت‌نشده یکسان است (ضد شمارش کاربران)."
        ),
        request=OtpRequestSerializer,
        responses={202: OtpRequestResultSerializer, 422: PROBLEM_422, 429: PROBLEM_429},
    )
    def post(self, request: Request) -> Response:
        """اعتبارسنجی ورودی، اعمال سقف‌ها و ارسال کد."""
        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.request_otp(
            phone=serializer.validated_data["phone"], request=request._request
        )
        return Response(OtpRequestResultSerializer(result).data, status=status.HTTP_202_ACCEPTED)


class OtpVerifyView(APIView):
    """تأیید کد ورود و باز کردن نشست کاربر."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_classes = [OtpVerifyThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="تأیید کد و ورود",
        description=(
            "با تأیید کد، نشست کاربر باز می‌شود. اگر پروفایل کامل نباشد، "
            "``next_step`` مقدار ``complete_profile`` می‌گیرد."
        ),
        request=OtpVerifySerializer,
        responses={200: SessionResultSerializer, 422: PROBLEM_422, 429: PROBLEM_429},
    )
    def post(self, request: Request) -> Response:
        """تأیید کد، ایجاد/بازیابی کاربر و باز کردن نشست."""
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verified = services.verify_otp(
            phone=serializer.validated_data["phone"],
            code=serializer.validated_data["code"],
            request=request._request,
        )
        auth_login(request._request, verified.user, backend=SESSION_BACKEND)
        payload = SessionResultSerializer.build(verified.user, is_new_user=verified.is_new_user)
        return Response(payload)


class LogoutView(APIView):
    """بستن نشست جاری."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["احراز هویت"],
        summary="خروج از حساب",
        request=None,
        responses={204: None, 401: PROBLEM_401},
    )
    def post(self, request: Request) -> Response:
        """ابطال نشست جاری و پاک‌سازی کوکی."""
        auth_logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """پروفایل کاربر جاری."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["پروفایل"],
        summary="پروفایل من",
        responses={200: ProfileSerializer, 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن نمای ایمن پروفایل کاربر جاری."""
        return Response(ProfileSerializer(request.user).data)


class ProfileUpdateView(APIView):
    """تکمیل یا ویرایش پروفایل — شرط باز شدن دسترسی رزرو."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["پروفایل"],
        summary="ثبت/ویرایش پروفایل",
        description=(
            "نام، نام خانوادگی، ملیت، استان، شهر و مدرک هویتی (کد ملی یا گذرنامه) را "
            "ثبت می‌کند. مدرک با AES-GCM رمزنگاری و ایندکس کور یکتا می‌شود (ضد حساب "
            "تکراری). همهٔ خطاها یک‌جا در ``errors`` (فیلدبه‌فیلد) برمی‌گردند."
        ),
        request=ProfileUpdateSerializer,
        responses={200: ProfileSerializer, 401: PROBLEM_401, 422: PROBLEM_422},
    )
    def patch(self, request: Request) -> Response:
        """به‌روزرسانی پروفایل از طریق سرویس هویت."""
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.complete_profile(
            user=authenticated_user(request),
            first_name=serializer.validated_data["first_name"],
            last_name=serializer.validated_data["last_name"],
            province=serializer.validated_data["province"],
            city=serializer.validated_data["city"],
            national_id=serializer.validated_data["national_id"],
            nationality=serializer.validated_data["nationality"],
            passport_number=serializer.validated_data["passport_number"],
            request=request._request,
        )
        return Response(ProfileSerializer(user).data)
