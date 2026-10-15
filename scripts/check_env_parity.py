#!/usr/bin/env python
"""بررسی هم‌خوانی ``.env.example`` با تنظیمات — مستندات کهنه، شکست CI است.

مسئله: کسی متغیر محیطی جدیدی اضافه می‌کند و فراموش می‌کند در نمونه مستند کند؛
سه ماه بعد، استقرار جدید شکست می‌خورد و کسی نمی‌داند چرا.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
SETTINGS = ROOT / "src" / "config" / "settings"

# متغیرهایی که در نمونه هستند ولی در تنظیمات خوانده نمی‌شوند (تحمل کنترل‌شده)
ALLOWED_EXTRA = {"MAHDIEH_ADMINS"}


def declared_in_example() -> set[str]:
    """نام متغیرهای تعریف‌شده در نمونه."""
    text = EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))


def read_in_settings() -> set[str]:
    """نام متغیرهایی که تنظیمات (یا کد) آن‌ها را می‌خواند."""
    names: set[str] = set()
    for path in SETTINGS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"""env\.\w+\(\s*["']([A-Z][A-Z0-9_]+)["']""", text))
    return names


def main() -> int:
    """اجرای بررسی."""
    example = declared_in_example()
    used = read_in_settings()

    missing_docs = sorted((used - example) - ALLOWED_EXTRA)
    unused_docs = sorted((example - used) - ALLOWED_EXTRA)

    ok = True
    if missing_docs:
        ok = False
        print("❌ این متغیرها در تنظیمات خوانده می‌شوند ولی در .env.example نیستند:")
        for name in missing_docs:
            print(f"   • {name}")
    if unused_docs:
        print("⚠️  این متغیرها در .env.example هستند ولی جایی خوانده نمی‌شوند:")
        for name in unused_docs:
            print(f"   • {name}")
    if ok:
        print(f"✅ هم‌خوانی محیط برقرار است ({len(used)} متغیر مستند).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
