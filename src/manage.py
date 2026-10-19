#!/usr/bin/env python
"""نقطهٔ ورود مدیریتی Django برای پروژهٔ مهدیه."""

from __future__ import annotations

import os
from pathlib import Path
import sys

#: پیش‌فرض محلی؛ روی سرور، فایل محیط (``src/.env``) ماژول تولید را تعیین می‌کند.
DEFAULT_SETTINGS = "config.settings.development"


def settings_module_from_env_file(env_file: Path) -> str | None:
    """``DJANGO_SETTINGS_MODULE`` از فایل محیط — بدون اجرای فایل و بی‌اتکا به پوسته.

    چرا؟ تنظیمات، فایل محیط را **پس از** انتخاب ماژول می‌خوانند. بی‌این خواندنِ
    زودهنگام، هر ``manage.py`` روی سرور (crontab، اسکریپت پشتیبان، اپراتور) بی‌صدا با
    تنظیمات توسعه اجرا می‌شد و تنها دروازهٔ انتشار جلویش را می‌گرفت.
    """
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found: str | None = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == "DJANGO_SETTINGS_MODULE":
            found = value.strip().strip("'\"") or None
    return found


def main() -> None:
    """اجرای دستور مدیریتی: متغیر محیطی ⇐ فایل محیط ⇐ پیش‌فرض توسعه."""
    if "DJANGO_SETTINGS_MODULE" not in os.environ:
        from_file = settings_module_from_env_file(Path(__file__).resolve().parent / ".env")
        os.environ["DJANGO_SETTINGS_MODULE"] = from_file or DEFAULT_SETTINGS

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - فقط در نصب ناقص رخ می‌دهد
        msg = "Django در دسترس نیست. آیا محیط را نصب کرده‌اید؟ راهنما: `uv sync --all-extras`"
        raise ImportError(msg) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
