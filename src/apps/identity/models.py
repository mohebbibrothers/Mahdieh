"""مدل‌های هویت — کاربر تلفن‌محور (بدون ایمیل اجباری).

تصمیم ADR-0004: شناسهٔ ورود = شمارهٔ تلفن. ایمیل اختیاری و تنها برای اطلاع‌رسانی.
کد ملی در فاز ۱ با رمزنگاری AES-GCM و ایندکس HMAC اضافه می‌شود؛ در این فاز
فقط ستون‌های پایه و «وضعیت تکمیل پروفایل» که شرط باز شدن رزرو است.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.uuid7 import uuid7
from apps.identity.choices import UserRole

if TYPE_CHECKING:
    pass


class UserManager(BaseUserManager["User"]):
    """مدیر کاربر — تلفن شناسهٔ ورود است، نه ایمیل."""

    use_in_migrations = True

    def _normalize_phone(self, phone: str) -> str:
        """نرمال‌سازی شمارهٔ تلفن به قالب ``+98XXXXXXXXXX``."""
        digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        digits = digits.removeprefix("0098")
        if digits.startswith("+98"):
            digits = digits[3:]
        elif digits.startswith("98") and len(digits) == 12:  # noqa: PLR2004
            digits = digits[2:]
        elif digits.startswith("0"):
            digits = digits[1:]
        if not digits.startswith("9") or len(digits) != 10:  # noqa: PLR2004
            msg = f"شمارهٔ تلفن معتبر نیست: {phone!r}"
            raise ValueError(msg)
        return f"+98{digits}"

    def create_user(self, phone: str, *, password: str | None = None, **extra: Any) -> User:
        """ساخت کاربر با تلفن نرمال‌شده."""
        if not phone:
            msg = "شمارهٔ تلفن الزامی است."
            raise ValueError(msg)
        user = self.model(phone=self._normalize_phone(phone), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, *, password: str, **extra: Any) -> User:
        """ساخت مدیر ارشد با نقش و اختیارات کامل."""
        extra.setdefault("role", UserRole.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("profile_completed_at", timezone.now())
        return self.create_user(phone, password=password, **extra)

    def by_phone(self, phone: str) -> User | None:
        """یافتن کاربر با تلفن در هر قالب نوشتاری."""
        try:
            normalized = self._normalize_phone(phone)
        except ValueError:
            return None
        return self.filter(phone=normalized, deleted_at__isnull=True).first()


class User(AbstractBaseUser, PermissionsMixin):
    """کاربر مهدیه — زائر یا کارمند.

    نکتهٔ امنیتی: ``phone`` یکتاست و به‌عنوان شناسهٔ ورود استفاده می‌شود.
    نمایش عمومی آن هرگز کامل نیست (پوشانده می‌شود).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False, verbose_name=_("شناسه"))
    phone = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        verbose_name=_("شمارهٔ تلفن"),
        help_text=_("شناسهٔ ورود؛ قالب نرمال: +98XXXXXXXXXX"),
    )
    email = models.EmailField(blank=True, default="", verbose_name=_("ایمیل (اختیاری)"))
    first_name = models.CharField(max_length=50, blank=True, default="", verbose_name=_("نام"))
    last_name = models.CharField(
        max_length=50, blank=True, default="", verbose_name=_("نام خانوادگی")
    )
    city = models.CharField(max_length=60, blank=True, default="", verbose_name=_("شهر"))
    province = models.CharField(max_length=40, blank=True, default="", verbose_name=_("استان"))
    role = models.CharField(
        max_length=16,
        choices=UserRole.choices,
        default=UserRole.GUEST,
        db_index=True,
        verbose_name=_("نقش"),
    )

    is_active = models.BooleanField(default=True, verbose_name=_("فعال"))
    is_staff = models.BooleanField(default=False, verbose_name=_("دسترسی پنل"))
    is_phone_verified = models.BooleanField(default=False, verbose_name=_("تلفن تأییدشده"))
    profile_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("زمان تکمیل پروفایل"),
        help_text=_("تا زمانی که این مقدار تهی است، دسترسی رزرو باز نمی‌شود."),
    )
    deleted_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name=_("حذف نرم")
    )
    date_joined = models.DateTimeField(default=timezone.now, verbose_name=_("زمان عضویت"))
    last_login_ip = models.GenericIPAddressField(
        null=True, blank=True, verbose_name=_("آخرین آی‌پی")
    )

    objects = UserManager()
    all_objects = models.Manager["User"]()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = _("کاربر")
        verbose_name_plural = _("کاربران")
        ordering = ("-date_joined",)
        indexes = [
            models.Index(fields=["role", "is_active"], name="user_role_active_idx"),
            models.Index(fields=["profile_completed_at"], name="user_profile_done_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = []

    def __str__(self) -> str:
        """نمای خوانا — تلفن پوشانده می‌شود."""
        return f"{self.display_name} ({self.masked_phone})"

    @property
    def display_name(self) -> str:
        """نام نمایشی کاربر."""
        full = f"{self.first_name} {self.last_name}".strip()
        return full or "کاربر مهدیه"

    @property
    def masked_phone(self) -> str:
        """نمای پوشاندهٔ تلفن — برای نمایش در پنل و لاگ."""
        if len(self.phone) < 7:  # noqa: PLR2004
            return "***"
        return f"{self.phone[:4]}***{self.phone[-4:]}"

    @property
    def is_profile_complete(self) -> bool:
        """آیا دسترسی رزرو باز است؟"""
        return self.profile_completed_at is not None

    @property
    def is_staff_member(self) -> bool:
        """آیا عضو کارکنان است؟"""
        return self.role in UserRole.staff_roles() or self.is_staff

    def complete_profile(
        self, *, first_name: str, last_name: str, province: str, city: str
    ) -> None:
        """تکمیل پروفایل و باز کردن دسترسی رزرو."""
        self.first_name = first_name.strip()
        self.last_name = last_name.strip()
        self.province = province.strip()
        self.city = city.strip()
        self.profile_completed_at = timezone.now()
        self.save(
            update_fields=[
                "first_name",
                "last_name",
                "province",
                "city",
                "profile_completed_at",
            ]
        )
