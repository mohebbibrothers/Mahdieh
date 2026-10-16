"""مدل‌های هویت — کاربر تلفن‌محور (بدون ایمیل اجباری).

تصمیم ADR-0004: شناسهٔ ورود = شمارهٔ تلفن. ایمیل اختیاری و تنها برای اطلاع‌رسانی.

کد ملی — دادهٔ «حساس» در طبقه‌بندی بخش ۷.۳ سند معماری:
    • ذخیره‌سازی: رمزنگاری‌شده (AES-GCM) در ``national_id_encrypted``.
    • یکتایی و جست‌وجو: ایندکس کور HMAC در ``national_id_blind`` با قید یکتای
      جزئی — نه برای محرمانگی، بلکه برای «ضد حساب تکراری» (ثابت I12).
    • نمایش: همیشه پوشانده؛ خواندن کامل فقط با سرویس ممیزی‌شده.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.crypto import blind_index
from apps.core.fields import EncryptedCharField
from apps.core.uuid7 import uuid7
from apps.identity.choices import OtpPurpose, UserRole
from apps.identity.validators import mask_national_id, normalize_phone

if TYPE_CHECKING:
    pass

NATIONAL_ID_NAMESPACE = "national_id"
OTP_CODE_NAMESPACE = "otp_code"


class UserManager(BaseUserManager["User"]):
    """مدیر کاربر — تلفن شناسهٔ ورود است، نه ایمیل."""

    use_in_migrations = True

    def _normalize_phone(self, phone: str) -> str:
        """نرمال‌سازی شمارهٔ تلفن به قالب ``+98XXXXXXXXXX``.

        خطای اعتبارسنجی دامنه به ``ValueError`` نگاشت می‌شود تا قرارداد
        تاریخی این متد (که تست‌ها و ``by_phone`` بر آن تکیه دارند) نشکند.
        """
        try:
            return normalize_phone(phone)
        except DjangoValidationError as exc:
            msg = f"شمارهٔ تلفن معتبر نیست: {phone!r}"
            raise ValueError(msg) from exc

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
    national_id_encrypted = EncryptedCharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("کد ملی (رمزنگاری‌شده)"),
        help_text=_("متن روشن هرگز ذخیره نمی‌شود؛ فقط با سرویس ممیزی‌شده خوانده می‌شود."),
    )
    national_id_blind = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("ایندکس کور کد ملی"),
        help_text=_("HMAC برای جست‌وجو و تضمین یکتایی، بدون افشای مقدار"),
    )
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
    redacted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("زمان کمینه‌سازی داده"),
        help_text=_(
            "پس از پایان پنجرهٔ نگهداری، داده‌های شناسایی‌کننده پاک و این مهر ثبت می‌شود. "
            "شمارهٔ تلفن — که شناسهٔ ورود است — دست‌نخورده می‌ماند."
        ),
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
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["national_id_blind"],
                condition=~Q(national_id_blind=""),
                name="uniq_user_national_id_blind",
            ),
        ]

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
    def masked_national_id(self) -> str:
        """نمای پوشاندهٔ کد ملی — چهار رقم آخر، بدون رمزگشایی در حافظهٔ بلندمدت."""
        plain = self.national_id_encrypted or ""
        return mask_national_id(plain) if plain else "—"

    @property
    def is_profile_complete(self) -> bool:
        """آیا دسترسی رزرو باز است؟"""
        return self.profile_completed_at is not None

    @property
    def is_staff_member(self) -> bool:
        """آیا عضو کارکنان است؟"""
        return self.role in UserRole.staff_roles() or self.is_staff

    @property
    def is_manager(self) -> bool:
        """آیا اختیار حاکمیتی دارد (تغییر سیاست، خروجی کامل)؟

        این پرچم یک منبع حقیقت است: نماها و APIها به‌جای مقایسهٔ مستقیم نقش،
        همین را می‌پرسند تا اگر روزی سلسله‌مراتب نقش‌ها تغییر کرد، یک نقطه
        به‌روزرسانی شود.
        """
        return self.role in UserRole.manager_roles() or self.is_superuser

    def set_national_id(self, value: str) -> None:
        """ثبت کد ملی به‌صورت رمزنگاری‌شده + ایندکس کور (بدون ذخیره در این متد)."""
        self.national_id_encrypted = value
        self.national_id_blind = blind_index(value, namespace=NATIONAL_ID_NAMESPACE)

    def reveal_national_id(self) -> str:
        """خواندن متن روشن کد ملی — فقط در سرویس‌های ممیزی‌شده فراخوانی شود."""
        return self.national_id_encrypted or ""

    def complete_profile(
        self,
        *,
        first_name: str,
        last_name: str,
        province: str,
        city: str,
        national_id: str | None = None,
    ) -> None:
        """تکمیل پروفایل و باز کردن دسترسی رزرو."""
        self.first_name = first_name.strip()
        self.last_name = last_name.strip()
        self.province = province.strip()
        self.city = city.strip()
        if national_id:
            self.set_national_id(national_id)
        self.profile_completed_at = timezone.now()
        self.save(
            update_fields=[
                "first_name",
                "last_name",
                "province",
                "city",
                "national_id_encrypted",
                "national_id_blind",
                "profile_completed_at",
            ]
        )


class OtpChallenge(models.Model):
    """چالش کد یک‌بارمصرف — راز در دیتابیس ذخیره نمی‌شود، فقط هش آن.

    طراحی:
        • ``code_hash`` = HMAC-SHA256(code) با کلید محرمانهٔ سرور؛ حتی با دسترسی
          خواندن دیتابیس، کد قابل بازگردانی نیست (بدون پیمایش ساده).
        • ``attempts``/``max_attempts`` ⇒ سقف تلاش و ضدهجوم (سناریوی S8).
        • ``purpose`` ⇒ جداسازی زمینه‌ها؛ کد ورود برای تغییر شماره قابل استفاده نیست.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False, verbose_name=_("شناسه"))
    phone = models.CharField(max_length=16, db_index=True, verbose_name=_("شمارهٔ تلفن"))
    code_hash = models.CharField(max_length=64, verbose_name=_("هش کد"))
    purpose = models.CharField(
        max_length=24,
        choices=OtpPurpose.choices,
        default=OtpPurpose.LOGIN,
        db_index=True,
        verbose_name=_("کاربرد"),
    )
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name=_("تلاش‌های ناموفق"))
    max_attempts = models.PositiveSmallIntegerField(default=5, verbose_name=_("سقف تلاش"))
    expires_at = models.DateTimeField(db_index=True, verbose_name=_("انقضا"))
    consumed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان مصرف"))
    request_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("آی‌پی درخواست"))
    user_agent = models.CharField(max_length=300, blank=True, default="", verbose_name=_("مرورگر"))
    created_at = models.DateTimeField(
        default=timezone.now, db_index=True, verbose_name=_("زمان ایجاد")
    )

    class Meta:
        verbose_name = _("چالش کد یک‌بارمصرف")
        verbose_name_plural = _("چالش‌های کد یک‌بارمصرف")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["phone", "purpose", "created_at"], name="otp_phone_purpose_idx"),
            models.Index(fields=["expires_at", "consumed_at"], name="otp_expiry_idx"),
        ]

    def __str__(self) -> str:
        """نمای خوانا — شماره پوشانده می‌شود."""
        from apps.identity.validators import mask_phone

        return f"OTP {self.purpose} → {mask_phone(self.phone)}"

    @property
    def is_expired(self) -> bool:
        """آیا مهلت کد گذشته است؟"""
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        """آیا این چالش هنوز قابل استفاده است؟"""
        return (
            self.consumed_at is None and not self.is_expired and self.attempts < self.max_attempts
        )

    def register_failure(self) -> int:
        """ثبت تلاش ناموفق و بازگرداندن تعداد تلاش‌های باقی‌مانده."""
        self.attempts += 1
        self.save(update_fields=["attempts"])
        return max(self.max_attempts - self.attempts, 0)

    def consume(self) -> None:
        """مصرف موفق کد — چالش دیگر قابل استفاده نیست."""
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])
