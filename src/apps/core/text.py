"""ابزارهای متنی مشترک — ارقام فارسی و لاتین در یک نقطهٔ حقیقت.

چرا در ``core``؟ سه لایه به این تبدیل نیاز دارند: لایهٔ نمایش (قالب‌ها و
فیلترها)، نمودارهای SVG داشبورد، و لایهٔ پیامک (پنل‌های ایرانی پیام را با
ارقام فارسی می‌پسندند). اگر هر لایه جدول ارقام خودش را بسازد، روزی یکی از
آن‌ها جدا می‌افتد و قاعدهٔ «هیچ عدد لاتینی در خروجی زائر» می‌شکند — همان
قاعده‌ای که در تست‌ها تضمین می‌شود.

این ماژول عمداً هیچ وابستگی‌ای جز استاندارد پایتون ندارد.
"""

from __future__ import annotations

import re
from typing import Any, Final

ASCII_DIGITS: Final[str] = "0123456789"
PERSIAN_DIGITS_TEXT: Final[str] = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS_TEXT: Final[str] = "٠١٢٣٤٥٦٧٨٩"
PERSIAN_DECIMAL_SEPARATOR: Final[str] = "٫"
LATIN_DECIMAL_SEPARATOR: Final[str] = "."

# جدول تبدیل برای ``str.translate`` — کلیدها باید کد کاراکتر باشند.
PERSIAN_DIGITS: Final[dict[int, str]] = {
    ord(digit): persian for digit, persian in zip(ASCII_DIGITS, PERSIAN_DIGITS_TEXT, strict=True)
}
LATIN_DIGITS: Final[dict[int, int]] = str.maketrans(
    PERSIAN_DIGITS_TEXT + ARABIC_DIGITS_TEXT + PERSIAN_DECIMAL_SEPARATOR,
    ASCII_DIGITS * 2 + LATIN_DECIMAL_SEPARATOR,
)


def to_persian_digits(value: Any) -> str:
    """تبدیل هر مقدار به رشته با ارقام فارسی (اعداد لاتین ⇒ فارسی)."""
    return str(value).translate(PERSIAN_DIGITS)


def to_persian_number(value: float, *, digits: int = 1) -> str:
    """عدد اعشاری برای چشم فارسی‌زبان: «۳٫۵» — ارقام فارسی و ممیز «٫».

    چرا جدا از ``to_persian_digits``؟ چون ممیز لاتین در متن فارسی نقص است، ولی
    تبدیل کورکورانهٔ نقطه، لینک و نسخه و نام فایل را خراب می‌کند. پس اینجا فقط
    جایی است که **می‌دانیم** ورودی عدد است.
    """
    text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    return to_persian_digits(text).replace(LATIN_DECIMAL_SEPARATOR, PERSIAN_DECIMAL_SEPARATOR)


def to_latin_digits(value: Any) -> str:
    """تبدیل ارقام فارسی/عربی و ممیز فارسی به معادل لاتین (ورودی کاربر)."""
    return str(value).translate(LATIN_DIGITS)


# ─── یکسان‌سازی متن فارسی (جست‌وجو، تطبیق، کلید) ─────────────────────────────
# چرا؟ کاربر «ك» و «ي» عربی، نیم‌فاصله، اعراب، یا نویسه‌های نامرئی (RTL/LTR)
# تایپ می‌کند. بدون یکسان‌سازی، جست‌وجوی «رزرو» واژهٔ «رزرو» را پیدا نمی‌کند و
# نتیجه این است که کاربر فکر می‌کند «سامانه چیزی نمی‌داند» — بدتر از نبودِ جست‌وجو.
ARABIC_TO_PERSIAN: Final[dict[int, str]] = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ئ": "ی",
        "ك": "ک",
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
        "ﻻ": "لا",
    }
)
DIACRITICS: Final[re.Pattern[str]] = re.compile(r"[\u064B-\u0652\u0670\u0640]")
INVISIBLE: Final[re.Pattern[str]] = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
ZERO_WIDTH_NON_JOINER: Final[str] = "\u200c"
SEARCH_TOKEN: Final[re.Pattern[str]] = re.compile(r"[^\w\u0600-\u06FF]+")


def normalize_fa(value: Any) -> str:
    """یکسان‌سازی متن فارسی **برای جست‌وجو** (نه برای نمایش).

    خروجی: ارقام لاتین، «ی/ک» فارسی، بدون اعراب و نیم‌فاصله و نویسهٔ نامرئی،
    فاصله‌های جمع‌شده و حروف کوچک. متن نمایشی هرگز از این تابع نمی‌گذرد؛ پس
    شکل ظاهری راهنما و پیام‌ها دست‌نخورده می‌ماند.
    """
    text = to_latin_digits(value)
    text = text.translate(ARABIC_TO_PERSIAN)
    text = DIACRITICS.sub("", text)
    text = INVISIBLE.sub("", text)
    text = text.replace(ZERO_WIDTH_NON_JOINER, "")
    return WHITESPACE.sub(" ", text).strip().lower()


def search_tokens(value: Any) -> list[str]:
    """واژه‌های جست‌وجو از متن کاربر — با یکسان‌سازی، بدون نشانه‌گذاری.

    «نیم‌روز؟» و «نیم روز» و «نیمروز» همه به یک نشانه می‌رسند؛ پس کاربر لازم
    نیست شکل دقیق واژه را بداند.
    """
    return [token for token in SEARCH_TOKEN.split(normalize_fa(value)) if token]
