#!/usr/bin/env python
"""تولید فهرست کشورها از CLDR — منبع یگانهٔ «ملیت» در پروفایل زائران خارجی.

چرا تولیدی و ایستا؟ نام فارسی کشورها نباید دستی تایپ شود (غلط املایی، کشور
جاافتاده)، ولی Babel هم نباید وابستگی زمان اجرای سرور شود. پس این اسکریپت
یک‌بار با CLDR اجرا می‌شود و ماژول پایتونِ قطعی می‌سازد که در مخزن ثبت است:

    uvx --with babel python scripts/generate_countries.py

خروجی: ``src/apps/identity/countries.py`` (کد ISO 3166-1 alpha-2، نام فارسی،
نام انگلیسی) — مرتب با ترتیب الفبای فارسی و با کشورهای پرتکرار زائران در صدر.
"""

from __future__ import annotations

from pathlib import Path

from babel import Locale, __version__ as babel_version

ISO_ALPHA2_LENGTH = 2
ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "apps" / "identity" / "countries.py"

#: کدهای CLDR که کشور ISO 3166-1 نیستند (قلمروهای ویژه/گروه‌ها/کدهای کاربری).
NON_ISO = {"AC", "CP", "CQ", "DG", "EA", "IC", "TA", "EU", "EZ", "UN", "QO", "XA", "XB", "XK", "ZZ"}

#: پرتکرارترین ملیت‌های زائران حرم رضوی — در صدر فهرست انتخاب نشان داده می‌شوند.
PRIORITY = ("IR", "IQ", "AF", "PK", "AZ", "LB", "BH", "KW", "SA", "SY", "IN", "TR", "TJ", "OM")

PERSIAN_ORDER = "آاأإبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیئ"


def persian_key(text: str) -> tuple[int, ...]:
    """کلید مرتب‌سازی با ترتیب الفبای فارسی (یونیکد «پ، چ، ژ، گ» را ته می‌برد)."""
    normalized = text.replace("ي", "ی").replace("ك", "ک")
    return tuple(
        PERSIAN_ORDER.index(char) if char in PERSIAN_ORDER else 100 + ord(char)
        for char in normalized
    )


def main() -> None:
    """ساخت و نوشتن ماژول کشورها."""
    fa = Locale("fa").territories
    en = Locale("en").territories
    codes = [
        code
        for code in fa
        if len(code) == ISO_ALPHA2_LENGTH
        and code.isalpha()
        and code.isupper()
        and code not in NON_ISO
    ]
    rows = sorted(codes, key=lambda code: persian_key(fa[code]))
    lines = [
        '"""فهرست کشورها (ISO 3166-1 alpha-2) با نام فارسی و انگلیسی — **تولیدی**.',
        "",
        "این فایل را دستی ویرایش نکنید؛ با ``scripts/generate_countries.py`` از CLDR",
        f"(Babel {babel_version}) بازتولید می‌شود. ترتیب: الفبای فارسی.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        "#: (کد، نام فارسی، نام انگلیسی) — مرتب با الفبای فارسی.",
        "COUNTRIES: Final[tuple[tuple[str, str, str], ...]] = (",
    ]
    lines.extend(f'    ("{code}", "{fa[code]}", "{en[code]}"),' for code in rows)
    lines += [
        ")",
        "",
        "#: ملیت‌های پرتکرار زائران — برای صدر فهرست انتخاب در رابط کاربری.",
        f"PRIORITY_CODES: Final[tuple[str, ...]] = {PRIORITY!r}",
        "",
        "_BY_CODE: Final[dict[str, tuple[str, str, str]]] = {row[0]: row for row in COUNTRIES}",
        "",
        "",
        "def is_country(code: str) -> bool:",
        '    """آیا کد، یک کشور ISO 3166-1 شناخته‌شده است؟"""',
        '    return (code or "").upper() in _BY_CODE',
        "",
        "",
        "def country_title(code: str) -> str:",
        '    """نام فارسی کشور؛ کد ناشناخته ⇒ رشتهٔ تهی."""',
        '    row = _BY_CODE.get((code or "").upper())',
        '    return row[1] if row else ""',
        "",
        "",
        "def country_payload() -> list[dict[str, object]]:",
        '    """نمای سریال‌پذیر برای ``/api/v1/config`` — پرتکرارها با پرچم ``priority``."""',
        "    priority = set(PRIORITY_CODES)",
        "    return [",
        '        {"code": code, "title": title, "title_en": title_en, "priority": code in priority}',
        "        for code, title, title_en in COUNTRIES",
        "    ]",
        "",
    ]
    TARGET.write_text("\n".join(lines), encoding="utf-8")
    missing = [code for code in PRIORITY if code not in codes]
    if missing:
        raise SystemExit(f"کد اولویت ناشناخته: {missing}")
    print(f"{len(rows)} کشور ⇒ {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
