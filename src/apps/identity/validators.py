"""اعتبارسنجی و نرمال‌سازی دادهٔ هویت — «ورودی کاربر را نجیب کن، نه سخت».

مسئلهٔ واقعی: زائر با کیبورد فارسی «۹۱۲۳۴۵۶۷۸۹» می‌نویسد، نامش را با «ي» و
«ك» عربی تایپ می‌کند، و کد ملی را با فاصله می‌چسباند. سامانه‌ای که این‌ها را
رد کند، «سخت‌گیر» نیست — «بی‌ادب» است. پس: نرمال‌سازی می‌کنیم، سپس اعتبار
سنجی سخت‌گیرانه روی مقدار نرمال‌شده انجام می‌دهیم.
"""

from __future__ import annotations

import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.text import to_latin_digits

PHONE_LENGTH = 10
NATIONAL_ID_LENGTH = 10
NAME_MIN_LENGTH = 2
NAME_MAX_LENGTH = 50
CITY_MAX_LENGTH = 60
_NATIONAL_ID_MODULUS = 11
_NATIONAL_ID_PLACE_BASE = 10
_NATIONAL_ID_SIMPLE_LIMIT = 2
_ALL_SAME_DIGIT = re.compile(r"^(\d)\1+$")
_NAME_ALLOWED = re.compile(r"^[\u0600-\u06FF\u200c\s\-'.]+$")
#: نام زائر خارجی: فارسی/عربی (همهٔ بلوک‌های عربی یونیکد) **یا** لاتین (با حروف
#: آکسان‌دار اروپایی/ترکی/آذری). عدد و نماد همچنان ممنوع است.
_NAME_ALLOWED_INTERNATIONAL = re.compile(
    r"^[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF"
    r"A-Za-z\u00C0-\u024F\u1E00-\u1EFF\u200c\s\-'.]+$"
)
PASSPORT_MIN_LENGTH = 5
PASSPORT_MAX_LENGTH = 20
_PASSPORT_ALLOWED = re.compile(r"^[A-Z0-9]+$")
E164_MIN_DIGITS = 8
E164_MAX_DIGITS = 15
IRAN_COUNTRY_CODE = "98"


def normalize_digits(value: str) -> str:
    """تبدیل ارقام فارسی/عربی به ارقام لاتین و حذف فاصله‌های اضافی.

    کاربر ممکن است شماره را با ارقام فارسی تایپ کند (کیبورد فارسی) یا با ارقام
    عربی (Past/paste از پیام‌رسان)؛ هر دو باید به یک شکل نرمال شوند.
    """
    return to_latin_digits(value).strip()


def normalize_phone(value: str) -> str:
    """نرمال‌سازی شمارهٔ تلفن همراه به قالب ``+98XXXXXXXXXX``.

    قالب‌های پذیرفته‌شده: ``09123456789`` · ``9123456789`` · ``+989123456789``
    · ``00989123456789`` · ``989123456789`` · با ارقام فارسی/عربی و فاصله/خط تیره.
    """
    compact = re.sub(r"[\s\-().]+", "", normalize_digits(value))
    international = compact.startswith(("+", "00"))
    digits = compact[2:] if compact.startswith("00") else compact.removeprefix("+")
    if international and not digits.startswith(IRAN_COUNTRY_CODE):
        return _normalize_international_phone(digits)
    for prefix in ("0098", "98"):
        if digits.startswith(prefix) and len(digits) > PHONE_LENGTH:
            digits = digits[len(prefix) :]
            break
    digits = digits.removeprefix("0")
    if not digits.startswith("9") or len(digits) != PHONE_LENGTH:
        raise ValidationError(_("شمارهٔ تلفن همراه معتبر نیست."), code="invalid_phone")
    return f"+98{digits}"


def _normalize_international_phone(digits: str) -> str:
    """شمارهٔ غیرایرانی به قالب E.164 (``+<کد کشور><شماره>``) — پشت پرچم تنظیمات.

    پیش‌فرض خاموش است: پنل پیامک داخلی معمولاً به شمارهٔ خارجی تحویل نمی‌دهد و
    زائری که کد ورود دریافت نکند، پشت در می‌ماند. زائر خارجی با سیم‌کارت ایرانی
    (رایج‌ترین حالت) همیشه می‌تواند ثبت‌نام کند؛ ملیت و گذرنامه مستقل از تلفن‌اند.
    """
    if not getattr(settings, "INTERNATIONAL_PHONES_ENABLED", False):
        raise ValidationError(
            _(
                "فعلاً فقط شماره‌های همراه ایران پذیرفته می‌شود؛ زائران گرامی خارجی "
                "می‌توانند با سیم‌کارت ایرانی ثبت‌نام کنند."
            ),
            code="international_phone_disabled",
        )
    if (
        not digits.isdigit()
        or digits.startswith("0")
        or not (E164_MIN_DIGITS <= len(digits) <= E164_MAX_DIGITS)
    ):
        raise ValidationError(_("شمارهٔ تلفن بین‌المللی معتبر نیست."), code="invalid_phone")
    return f"+{digits}"


def is_valid_phone(value: str) -> bool:
    """آیا این مقدار یک شمارهٔ تلفن همراه معتبر است؟"""
    try:
        normalize_phone(value)
    except ValidationError:
        return False
    return True


def normalize_person_name(value: str) -> str:
    """یکسان‌سازی حروف عربی/فارسی، حذف نیم‌فاصلهٔ اضافی و جمع‌کردن فاصله‌ها."""
    text = unicodedata.normalize("NFC", value or "")
    text = text.replace("ي", "ی").replace("ك", "ک").replace("\u064a", "ی")
    text = text.replace("\u0643", "ک").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", " ", text).strip()


def validate_person_name(value: str, *, field: str = "نام", international: bool = False) -> str:
    """اعتبارسنجی نام/نام خانوادگی: طول و نویسه‌های مجاز.

    ``international=True`` (زائر غیرایرانی) حروف لاتین را هم می‌پذیرد تا نام
    دقیقاً مطابق گذرنامه ثبت شود؛ برای اتباع ایران نام فارسی کارت ملی ملاک است.
    """
    text = normalize_person_name(value)
    if not (NAME_MIN_LENGTH <= len(text) <= NAME_MAX_LENGTH):
        raise ValidationError(
            _("%(field)s باید بین ۲ تا ۵۰ نویسه باشد.") % {"field": field},
            code="invalid_name_length",
        )
    pattern = _NAME_ALLOWED_INTERNATIONAL if international else _NAME_ALLOWED
    if not pattern.match(text):
        raise ValidationError(
            _("%(field)s نویسه‌های غیرمجاز دارد.") % {"field": field},
            code="invalid_name_chars",
        )
    return text


def validate_national_id(value: str) -> str:
    """اعتبارسنجی کد ملی ۱۰ رقمی با الگوریتم رسمی رقم کنترلی.

    گام‌ها: نرمال‌سازی ارقام ⇒ رد الگوهای تکراری (``1111111111``) ⇒ محاسبهٔ
    رقم کنترلی با وزن‌های ۱۰..۲ و مبنای ۱۱ ⇒ مقایسه.
    """
    digits = normalize_digits(value)
    if not digits.isdigit() or len(digits) != NATIONAL_ID_LENGTH:
        raise ValidationError(_("کد ملی باید ۱۰ رقم باشد."), code="invalid_national_id_length")
    if _ALL_SAME_DIGIT.match(digits):
        raise ValidationError(_("کد ملی وارد‌شده معتبر نیست."), code="invalid_national_id")
    total = sum(
        int(digit) * (_NATIONAL_ID_PLACE_BASE - index) for index, digit in enumerate(digits[:9])
    )
    remainder = total % _NATIONAL_ID_MODULUS
    expected = (
        remainder if remainder < _NATIONAL_ID_SIMPLE_LIMIT else _NATIONAL_ID_MODULUS - remainder
    )
    if int(digits[9]) != expected:
        raise ValidationError(_("رقم کنترلی کد ملی درست نیست."), code="invalid_national_id_check")
    return digits


def validate_passport_number(value: str) -> str:
    """اعتبارسنجی شمارهٔ گذرنامه: ۵ تا ۲۰ نویسهٔ لاتین/رقم، بدون فاصله.

    قالب گذرنامه میان کشورها یکسان نیست (ICAO 9303 فقط سقف ۹ نویسهٔ MRZ را
    می‌گوید و برخی کشورها شمارهٔ بلندتر دارند)؛ پس فقط شکل کلی را می‌سنجیم و
    الگوهای آشکارا ساختگی (همه یک نویسه، بدون هیچ رقم) را رد می‌کنیم.
    """
    text = re.sub(r"[\s\-]+", "", normalize_digits(value)).upper()
    if not (PASSPORT_MIN_LENGTH <= len(text) <= PASSPORT_MAX_LENGTH):
        raise ValidationError(
            _("شمارهٔ گذرنامه باید بین ۵ تا ۲۰ نویسه باشد."), code="invalid_passport_length"
        )
    if (
        not _PASSPORT_ALLOWED.match(text)
        or not any(char.isdigit() for char in text)
        or len(set(text)) == 1
    ):
        raise ValidationError(_("شمارهٔ گذرنامه معتبر نیست."), code="invalid_passport")
    return text


def mask_national_id(value: str) -> str:
    """نمایش ایمن کد ملی/گذرنامه — فقط ۴ نویسهٔ آخر (قاعدهٔ کمینه‌سازی نمایش)."""
    digits = normalize_digits(value)
    if len(digits) < 4:  # noqa: PLR2004 - آستانهٔ نمایش ایمن
        return "***"
    return f"***{digits[-4:]}"


def mask_phone(value: str) -> str:
    """نمایش ایمن شمارهٔ تلفن — چهار رقم اول و چهار رقم آخر."""
    digits = normalize_digits(value)
    if len(digits) < 7:  # noqa: PLR2004 - آستانهٔ نمایش ایمن
        return "***"
    return f"{digits[:4]}***{digits[-4:]}"
