"""ابزارهای متنی مشترک — ارقام فارسی و لاتین در یک نقطهٔ حقیقت.

چرا در ``core``؟ سه لایه به این تبدیل نیاز دارند: لایهٔ نمایش (قالب‌ها و
فیلترها)، نمودارهای SVG داشبورد، و لایهٔ پیامک (پنل‌های ایرانی پیام را با
ارقام فارسی می‌پسندند). اگر هر لایه جدول ارقام خودش را بسازد، روزی یکی از
آن‌ها جدا می‌افتد و قاعدهٔ «هیچ عدد لاتینی در خروجی زائر» می‌شکند — همان
قاعده‌ای که در تست‌ها تضمین می‌شود.

این ماژول عمداً هیچ وابستگی‌ای جز استاندارد پایتون ندارد.
"""

from __future__ import annotations

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
