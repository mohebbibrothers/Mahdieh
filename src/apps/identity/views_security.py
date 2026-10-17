"""نماهای امنیت حساب — CSRF، تأیید دومرحله‌ای کارکنان و «دستگاه‌های من».

همهٔ پاسخ‌های این ماژول ``Cache-Control: no-store`` دارند: توکن CSRF، راز TOTP،
کدهای بازیابی و فهرست نشست‌ها هرگز نباید در هیچ کشی (مرورگر/CDN) بمانند.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import NotFoundError
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_404, PROBLEM_409, PROBLEM_422
from apps.identity import mfa, sessions
from apps.identity.permissions import authenticated_user
from apps.identity.throttles import SensitiveActionThrottle

NO_STORE = "no-store"
CSRF_HEADER_NAME = "X-CSRFToken"


def _no_store(response: Response) -> Response:
    response["Cache-Control"] = NO_STORE
    return response


# ── سریال‌ایزرها (قرارداد سند) ───────────────────────────────────────────────


class CsrfSerializer(serializers.Serializer[Any]):
    """توکن CSRF برای نوشتن‌های فرانت (کوکی هم‌زمان تنظیم می‌شود)."""

    csrf_token = serializers.CharField()
    header_name = serializers.CharField(help_text="نام هدری که توکن باید در آن بیاید")
    cookie_name = serializers.CharField()


class MfaStatusSerializer(serializers.Serializer[Any]):
    """وضعیت عامل دوم نشست جاری."""

    # «required» با ویژگی ``Field.required`` در استاب‌های DRF هم‌نام است؛ قرارداد
    # JSON مهم‌تر است، پس فقط نوع صریح می‌شود (همان الگوی ``label`` در تقویم).
    required = serializers.BooleanField(help_text="آیا این حساب مشمول عامل دوم است؟")  # type: ignore[assignment]
    enrolled = serializers.BooleanField()
    verified = serializers.BooleanField(help_text="آیا همین نشست تأیید شده است؟")
    verified_at = serializers.DateTimeField(allow_null=True)
    step_up_valid_until = serializers.DateTimeField(
        allow_null=True, help_text="تا این لحظه، کارهای پرخطر بدون تأیید دوباره مجازند"
    )
    step_up_seconds = serializers.IntegerField()
    recovery_codes_remaining = serializers.IntegerField()
    method = serializers.CharField(allow_blank=True)


class MfaEnrollmentSerializer(serializers.Serializer[Any]):
    """دادهٔ ثبت عامل دوم — فقط همین یک بار برمی‌گردد."""

    secret = serializers.CharField(help_text="راز Base32 برای ورود دستی")
    secret_display = serializers.CharField(help_text="همان راز در گروه‌های چهارتایی")
    otpauth_uri = serializers.CharField()
    qr_svg = serializers.CharField(help_text="QR به‌شکل SVG درون‌خطی")


class MfaCodeSerializer(serializers.Serializer[Any]):
    """کد شش‌رقمی اپ احراز هویت یا کد بازیابی ``XXXXX-XXXXX``."""

    code = serializers.CharField(min_length=6, max_length=16)


class MfaConfirmResultSerializer(serializers.Serializer[Any]):
    """نتیجهٔ فعال‌سازی: کدهای بازیابی (یک‌بار) + وضعیت تازه."""

    recovery_codes = serializers.ListField(child=serializers.CharField())
    status = MfaStatusSerializer()


class MfaVerifyResultSerializer(serializers.Serializer[Any]):
    """نتیجهٔ تأیید: روش استفاده‌شده + وضعیت تازه."""

    method = serializers.ChoiceField(choices=[mfa.METHOD_TOTP, mfa.METHOD_RECOVERY])
    status = MfaStatusSerializer()


class RecoveryCodesSerializer(serializers.Serializer[Any]):
    """کدهای بازیابی تازه (کدهای قبلی باطل شدند)."""

    recovery_codes = serializers.ListField(child=serializers.CharField())


class SessionItemSerializer(serializers.Serializer[Any]):
    """یک نشست فعال در فهرست «دستگاه‌های من»."""

    id = serializers.UUIDField()
    device = serializers.CharField(help_text="مثل «Chrome · Android»")
    network = serializers.CharField(help_text="پیشوند شبکه (نه آی‌پی کامل)")
    created_at = serializers.DateTimeField()
    last_seen_at = serializers.DateTimeField()
    is_current = serializers.BooleanField()


class SessionListSerializer(serializers.Serializer[Any]):
    """پوشش فهرست نشست‌ها."""

    results = SessionItemSerializer(many=True)


# ── CSRF ────────────────────────────────────────────────────────────────────


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """صدور توکن CSRF — نخستین درخواست فرانت پیش از هر نوشتن (یافتهٔ H5)."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["احراز هویت"],
        summary="دریافت توکن CSRF",
        description=(
            "کوکی ``csrftoken`` را تنظیم می‌کند و همان توکن را برمی‌گرداند. فرانت آن را "
            "در هدر ``X-CSRFToken`` همهٔ درخواست‌های نوشتنی می‌فرستد. پاسخ کش نمی‌شود."
        ),
        responses={200: CsrfSerializer},
    )
    def get(self, request: Request) -> Response:
        """ساخت/بازگرداندن توکن CSRF نشست."""
        return _no_store(
            Response(
                {
                    "csrf_token": get_token(request._request),
                    "header_name": CSRF_HEADER_NAME,
                    "cookie_name": settings.CSRF_COOKIE_NAME,
                }
            )
        )


# ── عامل دوم ────────────────────────────────────────────────────────────────


class MfaStatusView(APIView):
    """وضعیت عامل دوم برای نشست جاری."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["احراز هویت"],
        summary="وضعیت تأیید دومرحله‌ای",
        responses={200: MfaStatusSerializer, 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """وضعیت ثبت، تأیید نشست و پنجرهٔ تأیید تازه."""
        return _no_store(Response(mfa.status_for(request._request).as_dict()))


class MfaSetupView(APIView):
    """آغاز ثبت اپ احراز هویت (فقط کارکنان)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="آغاز ثبت اپ احراز هویت",
        description="راز تازه و QR برمی‌گرداند؛ تا تأیید نخستین کد، دستگاه فعال نمی‌شود.",
        request=None,
        responses={200: MfaEnrollmentSerializer, 403: PROBLEM_403, 409: PROBLEM_409},
    )
    def post(self, request: Request) -> Response:
        """ساخت راز و QR برای اسکن در اپ."""
        enrollment = mfa.begin_enrollment(user=authenticated_user(request))
        return _no_store(
            Response(
                {
                    "secret": enrollment.secret,
                    "secret_display": enrollment.secret_display,
                    "otpauth_uri": enrollment.otpauth_uri,
                    "qr_svg": enrollment.qr_svg,
                }
            )
        )


class MfaConfirmView(APIView):
    """تأیید نخستین کد و فعال‌سازی عامل دوم."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="فعال‌سازی اپ احراز هویت",
        description="کدهای بازیابی فقط در همین پاسخ برمی‌گردند؛ آن‌ها را جای امن نگه دارید.",
        request=MfaCodeSerializer,
        responses={
            200: MfaConfirmResultSerializer,
            403: PROBLEM_403,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request) -> Response:
        """تأیید کد، فعال‌سازی دستگاه و صدور کدهای بازیابی."""
        payload = MfaCodeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        codes = mfa.confirm_enrollment(
            request=request._request,
            user=authenticated_user(request),
            code=payload.validated_data["code"],
        )
        return _no_store(
            Response(
                {"recovery_codes": codes, "status": mfa.status_for(request._request).as_dict()}
            )
        )


class MfaVerifyView(APIView):
    """تأیید عامل دوم برای نشست جاری (یا تأیید تازه پیش از کار پرخطر)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="تأیید دومرحله‌ای",
        description=(
            "کد شش‌رقمی اپ یا یک کد بازیابی. پس از ۵ تلاش ناموفق، ۱۵ دقیقه قفل "
            "(``mfa_locked``). همین مسیر برای «تأیید تازه» پیش از کارهای پرخطر است."
        ),
        request=MfaCodeSerializer,
        responses={
            200: MfaVerifyResultSerializer,
            403: PROBLEM_403,
            409: PROBLEM_409,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request) -> Response:
        """تأیید کد و علامت‌گذاری نشست."""
        payload = MfaCodeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        method = mfa.verify(
            request=request._request,
            user=authenticated_user(request),
            code=payload.validated_data["code"],
        )
        return _no_store(
            Response({"method": method, "status": mfa.status_for(request._request).as_dict()})
        )


class MfaRecoveryCodesView(APIView):
    """ساخت دوبارهٔ کدهای بازیابی — نیازمند تأیید تازه."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="ساخت دوبارهٔ کدهای بازیابی",
        request=None,
        responses={200: RecoveryCodesSerializer, 403: PROBLEM_403, 409: PROBLEM_409},
    )
    def post(self, request: Request) -> Response:
        """ابطال کدهای قبلی و صدور ده کد تازه."""
        codes = mfa.regenerate_recovery_codes(
            request=request._request, user=authenticated_user(request)
        )
        return _no_store(Response({"recovery_codes": codes}))


# ── نشست‌ها ────────────────────────────────────────────────────────────────


class SessionListView(APIView):
    """فهرست نشست‌های فعال من («دستگاه‌های من»)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["پروفایل"],
        summary="دستگاه‌های من",
        responses={200: SessionListSerializer, 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """نشست‌های فعال کاربر، نشست جاری در صدر."""
        views = sessions.active_for(
            authenticated_user(request), current_key=request._request.session.session_key
        )
        return _no_store(Response({"results": [view.as_dict() for view in views]}))


class SessionRevokeView(APIView):
    """خروج یک دستگاه دیگر."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["پروفایل"],
        summary="خروج یک دستگاه",
        request=None,
        responses={204: None, 401: PROBLEM_401, 404: PROBLEM_404},
    )
    def post(self, request: Request, session_id: Any) -> Response:
        """ابطال نشست انتخابی (نشست جاری با «خروج» بسته می‌شود)."""
        revoked = sessions.revoke(
            user=authenticated_user(request), session_id=str(session_id), request=request._request
        )
        if not revoked:
            raise NotFoundError("این نشست فعال نیست یا نشست جاری شماست.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    """خروج از همهٔ دستگاه‌ها — دستگاه‌های دیگر و همین نشست."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveActionThrottle]

    @extend_schema(
        tags=["احراز هویت"],
        summary="خروج از همهٔ دستگاه‌ها",
        request=None,
        responses={204: None, 401: PROBLEM_401},
    )
    def post(self, request: Request) -> Response:
        """ابطال همهٔ نشست‌های دیگر و بستن نشست جاری."""
        sessions.revoke_others(user=authenticated_user(request), request=request._request)
        auth_logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)
