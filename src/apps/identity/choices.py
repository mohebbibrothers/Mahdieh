"""شمارشی‌های دامنهٔ هویت."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRole(models.TextChoices):
    """نقش کاربر — تعیین‌کنندهٔ مرز اختیارات.

    مرزها در سند معماری بخش ۷.۴ تعریف شده است:
        • GUEST: رزرو و لغو رزرو خود.
        • OPERATOR: پذیرش، تخلیه، عدم‌حضور، رزرو تلفنی.
        • MANAGER: سیاست‌ها، جابه‌جایی آزاد، خروجی اکسل.
        • ADMIN: مدیریت کاربران و دسترسی‌ها، حسابرسی.
    """

    GUEST = "guest", _("زائر")
    OPERATOR = "operator", _("پذیرش")
    MANAGER = "manager", _("مدیر")
    ADMIN = "admin", _("مدیر ارشد")

    @classmethod
    def staff_roles(cls) -> tuple[UserRole, ...]:
        """نقش‌هایی که به پنل کارکنان دسترسی دارند."""
        return (cls.OPERATOR, cls.MANAGER, cls.ADMIN)

    @classmethod
    def manager_roles(cls) -> tuple[UserRole, ...]:
        """نقش‌هایی با اختیار حاکمیتی (سیاست، اقدام‌های حساس).

        «مدیر ارشد» هم شمرده می‌شود: اگر راهبر سیستم نتواند سیاست را ببیند،
        اختیار در عمل قفل می‌شود.
        """
        return (cls.MANAGER, cls.ADMIN)


class OtpPurpose(models.TextChoices):
    """کاربرد یک کد یک‌بارمصرف — جداسازی زمینه‌ها علیه بازپخش کد."""

    LOGIN = "login", _("ورود به حساب")
    PROFILE_PHONE_CHANGE = "phone_change", _("تغییر شمارهٔ تلفن")


class Province(models.TextChoices):
    """فهرست بستهٔ ۳۱ استان ایران — منبع یکتا برای فرم پروفایل."""

    TEHRAN = "tehran", _("تهران")
    ALBORZ = "alborz", _("البرز")
    ARDABIL = "ardabil", _("اردبیل")
    ISFAHAN = "isfahan", _("اصفهان")
    ILAM = "ilam", _("ایلام")
    BUSHEHR = "bushehr", _("بوشهر")
    CHAHARMAHAL = "chaharmahal", _("چهارمحال و بختیاری")
    KHORASAN_SOUTH = "khorasan_south", _("خراسان جنوبی")
    KHORASAN_RAZAVI = "khorasan_razavi", _("خراسان رضوی")
    KHORASAN_NORTH = "khorasan_north", _("خراسان شمالی")
    KHUZESTAN = "khuzestan", _("خوزستان")
    ZANJAN = "zanjan", _("زنجان")
    SEMNAN = "semnan", _("سمنان")
    SISTAN = "sistan", _("سیستان و بلوچستان")
    FARS = "fars", _("فارس")
    QAZVIN = "qazvin", _("قزوین")
    QOM = "qom", _("قم")
    KURDISTAN = "kurdistan", _("کردستان")
    KERMAN = "kerman", _("کرمان")
    KERMANSHAH = "kermanshah", _("کرمانشاه")
    KOHGILUYEH = "kohgiluyeh", _("کهگیلویه و بویراحمد")
    GOLESTAN = "golestan", _("گلستان")
    GILAN = "gilan", _("گیلان")
    LORESTAN = "lorestan", _("لرستان")
    MAZANDARAN = "mazandaran", _("مازندران")
    MARKAZI = "markazi", _("مرکزی")
    HORMOZGAN = "hormozgan", _("هرمزگان")
    HAMEDAN = "hamedan", _("همدان")
    YAZD = "yazd", _("یزد")
    WEST_AZERBAIJAN = "west_azerbaijan", _("آذربایجان غربی")
    EAST_AZERBAIJAN = "east_azerbaijan", _("آذربایجان شرقی")

    @classmethod
    def label_of(cls, code: str) -> str:
        """برچسب فارسی یک کد استان (برای گزارش و خروجی)."""
        for member in cls:
            if member.value == code:
                return str(member.label)
        return code


class IdDocumentType(models.TextChoices):
    """نوع مدرک هویتی زائر — کد ملی برای اتباع ایران، گذرنامه برای زائران خارجی."""

    NATIONAL_ID = "national_id", _("کد ملی")
    PASSPORT = "passport", _("گذرنامه")


#: ملیت پیش‌فرض (ISO 3166-1 alpha-2) — زائران ایرانی بی‌هیچ گام اضافه.
DEFAULT_NATIONALITY = "IR"
