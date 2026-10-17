"""سریال‌ایزرهای هویت — قرارداد ورودی/خروجی API و سند OpenAPI.

قاعدهٔ امنیتی: هیچ سریال‌ایزری، متن روشن کد ملی یا کد یک‌بارمصرف را برنمی‌گرداند.
هیچ فیلد ورودی «نام کاربری/ایمیل» وجود ندارد — ورود فقط با تلفن است.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.identity.choices import DEFAULT_NATIONALITY, IdDocumentType, Province
from apps.identity.countries import country_title
from apps.identity.models import User
from apps.identity.validators import normalize_digits, normalize_phone


def _phone_error(exc: DjangoValidationError) -> str:
    """پیام خطای تلفن: پیام اختصاصی (مثل «شمارهٔ خارجی فعلاً پذیرفته نیست») یا نمونهٔ درست."""
    if getattr(exc, "code", "") == "international_phone_disabled":
        return str(exc.messages[0])
    return "شمارهٔ تلفن همراه معتبر نیست. مثال درست: ۰۹۱۲۳۴۵۶۷۸۹"


class OtpRequestSerializer(serializers.Serializer[Any]):
    """ورودی درخواست کد ورود."""

    phone = serializers.CharField(
        max_length=20,
        allow_blank=False,
        help_text="شمارهٔ تلفن همراه؛ قالب‌های ۰۹…، ۹…، +۹۸… و ارقام فارسی پذیرفته می‌شود.",
    )

    def validate_phone(self, value: str) -> str:
        """نرمال‌سازی شماره؛ خروجی همیشه ``+98XXXXXXXXXX`` است."""
        try:
            return normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_phone_error(exc)) from exc


class OtpVerifySerializer(serializers.Serializer[Any]):
    """ورودی تأیید کد یک‌بارمصرف."""

    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(
        max_length=8, min_length=4, help_text="کد یک‌بارمصرف ارسال‌شده با پیامک"
    )

    def validate_phone(self, value: str) -> str:
        """نرمال‌سازی شماره پیش از تطبیق با چالش."""
        try:
            return normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_phone_error(exc)) from exc

    def validate_code(self, value: str) -> str:
        """پذیرش ارقام فارسی و حذف فاصله‌ها از کد."""
        return normalize_digits(value).replace(" ", "")


class OtpRequestResultSerializer(serializers.Serializer[Any]):
    """پاسخ درخواست کد — یکسان برای همهٔ شماره‌ها (ضد شمارش کاربران)."""

    expires_in = serializers.IntegerField(help_text="مدت اعتبار کد به ثانیه")
    resend_after = serializers.IntegerField(help_text="زمان انتظار تا درخواست کد تازه (ثانیه)")
    delivery_status = serializers.CharField(help_text="سرنوشت پیامک در دفتر تحویل")


class ProfileSerializer(serializers.Serializer[Any]):
    """نمای پروفایل کاربر — همهٔ فیلدهای حساس پوشانده‌اند."""

    id = serializers.UUIDField(read_only=True)
    phone_masked = serializers.CharField(source="masked_phone", read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    province = serializers.CharField(read_only=True)
    province_title = serializers.SerializerMethodField()
    city = serializers.CharField(read_only=True)
    nationality = serializers.CharField(read_only=True)
    nationality_title = serializers.SerializerMethodField()
    id_document_type = serializers.ChoiceField(choices=IdDocumentType.choices, read_only=True)
    national_id_masked = serializers.CharField(source="masked_national_id", read_only=True)
    role = serializers.CharField(read_only=True)
    is_profile_complete = serializers.BooleanField(read_only=True)
    is_staff_member = serializers.BooleanField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)

    @extend_schema_field(serializers.CharField)
    def get_province_title(self, obj: User) -> str:
        """عنوان فارسی استان — سریال‌ایزر کمکی برای فرانت فارسی."""
        return Province.label_of(obj.province) if obj.province else ""

    @extend_schema_field(serializers.CharField)
    def get_nationality_title(self, obj: User) -> str:
        """نام فارسی کشور ملیت."""
        return country_title(obj.nationality)


class ProfileUpdateSerializer(serializers.Serializer[Any]):
    """ورودی تکمیل/ویرایش پروفایل — تبعهٔ ایران یا زائر خارجی (ADR-0019).

    قواعد وابسته به ملیت (کد ملی/استان برای ایران، گذرنامه برای خارجی) در سرویس
    دامنه سنجیده می‌شوند تا همهٔ خطاها یک‌جا و فیلدبه‌فیلد برگردند.
    """

    # فیلدهای متنی خالی را می‌پذیرند تا «الزامی‌بودن» هم در سرویس، همراه بقیهٔ
    # خطاها، یک‌جا گزارش شود (نه اینکه لایهٔ serializer زودتر و ناقص بایستد).
    first_name = serializers.CharField(max_length=50, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=50, allow_blank=True, default="")
    nationality = serializers.CharField(
        max_length=2,
        required=False,
        default=DEFAULT_NATIONALITY,
        help_text="کد کشور ISO 3166-1 alpha-2 (فهرست در /api/v1/config)؛ پیش‌فرض IR.",
    )
    province = serializers.ChoiceField(
        choices=Province.choices,
        required=False,
        allow_blank=True,
        default="",
        help_text="برای اتباع ایران الزامی؛ برای زائر خارجی نادیده گرفته می‌شود.",
    )
    city = serializers.CharField(max_length=60, allow_blank=True, default="")
    national_id = serializers.CharField(
        max_length=12,
        required=False,
        allow_blank=True,
        default="",
        help_text="کد ملی ۱۰ رقمی (اتباع ایران)؛ رمزنگاری‌شده ذخیره می‌شود.",
    )
    passport_number = serializers.CharField(
        max_length=24,
        required=False,
        allow_blank=True,
        default="",
        help_text="شمارهٔ گذرنامه (زائر خارجی)؛ رمزنگاری‌شده ذخیره می‌شود.",
    )


class SessionResultSerializer(serializers.Serializer[Any]):
    """پاسخ ورود موفق — پروفایل + گام بعدی کاربر."""

    profile = ProfileSerializer()
    is_new_user = serializers.BooleanField()
    next_step = serializers.ChoiceField(
        choices=["complete_profile", "home"],
        help_text="گام بعدی: تکمیل پروفایل یا رفتن به صفحهٔ رزرو",
    )

    @staticmethod
    def build(user: User, *, is_new_user: bool) -> dict[str, Any]:
        """ساخت بدنهٔ پاسخ بدون وابستگی به serializer در سرویس."""
        return {
            "profile": ProfileSerializer(user).data,
            "is_new_user": is_new_user,
            "next_step": "home" if user.is_profile_complete else "complete_profile",
        }
