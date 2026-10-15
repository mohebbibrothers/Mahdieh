#!/usr/bin/env python
"""نقطهٔ ورود مدیریتی Django برای پروژهٔ مهدیه."""

from __future__ import annotations

import os
import sys


def main() -> None:
    """اجرای دستور مدیریتی با تنظیمات پیش‌فرض توسعه."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - فقط در نصب ناقص رخ می‌دهد
        msg = "Django در دسترس نیست. آیا محیط را نصب کرده‌اید؟ راهنما: `uv sync --all-extras`"
        raise ImportError(msg) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
