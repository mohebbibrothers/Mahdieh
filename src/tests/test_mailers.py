"""ایمیل عملیاتی — پیکربندی ``MAILERS`` باید پیش از استقرار سنجیده شود.

یافتهٔ واقعی (۱۸ آبان ۱۴۰۵): تنظیمات تولید کلیدهای OPTIONS را با حروف بزرگ نوشته بود
(HOST/USER/…)؛ جنگو ۶٫۱ هر ارسال را با ``InvalidMailer: Unknown options`` رد می‌کرد و
هشدار پایش سلامت و ایمیل خطای ۵۰۰ هرگز نمی‌رسید — بی هیچ نشانه‌ای جز یک سطر لاگ.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
from django.test import override_settings

from apps.core.checks import MAILER_ERROR, mailers_check

SRC = Path(__file__).resolve().parents[1]
SMTP = "django.core.mail.backends.smtp.EmailBackend"


def test_uppercase_or_unknown_options_and_missing_backends_are_errors() -> None:
    mailers = {
        "default": {"BACKEND": SMTP, "OPTIONS": {"HOST": "localhost", "USER": "x"}},
        "ghost": {"BACKEND": "no.such.Backend"},
    }
    with override_settings(MAILERS=mailers):
        issues = mailers_check()
    assert [issue.id for issue in issues] == [MAILER_ERROR, MAILER_ERROR]
    assert "Unknown options" in issues[0].msg
    assert "host" in issues[0].hint


def test_valid_mailers_pass() -> None:
    options = {"host": "localhost", "port": 25, "username": "", "password": "", "timeout": 10}
    with override_settings(MAILERS={"default": {"BACKEND": SMTP, "OPTIONS": options}}):
        assert mailers_check() == []


def test_the_real_production_settings_build_an_smtp_mailer() -> None:
    """ماژول تنظیمات تولید واقعی (نه کپی‌اش) در فرآیند جدا: بک‌اند SMTP ساخته می‌شود."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "MAHDIEH_ENVIRONMENT": "production",
        "MAHDIEH_SECRET_KEY": "k" * 64,
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "FIELD_HASH_KEY": "h" * 48,
        "DATABASE_URL": "mysql://u:p@127.0.0.1:3306/none",
        "MAHDIEH_ALLOWED_HOSTS": "gam-khedmat.ir",
        "MAHDIEH_SITE_URL": "https://gam-khedmat.ir",
        "SMS_ENABLED": "false",
        "EMAIL_HOST": "localhost",
        "EMAIL_PORT": "587",
        "EMAIL_USE_TLS": "true",
        # مقدار تهی = «بساز از دامنهٔ سایت» (همان رفتار فایل محیطِ بی این کلیدها). صریح است
        # چون read_env فایل ‎.env‎ محلی را فقط برای کلیدهای ناموجود می‌خواند.
        "DEFAULT_FROM_EMAIL": "",
        "SERVER_EMAIL": "",
    }
    code = (
        "import django; django.setup()\n"
        "from django.core import mail\n"
        "from apps.core.checks import mailers_check\n"
        "m = mail.mailers.default\n"
        "from django.conf import settings as s\n"
        "print(type(m).__name__, m.host, m.port, m.use_tls, m.timeout, len(mailers_check()))\n"
        "print(s.DEFAULT_FROM_EMAIL, s.SERVER_EMAIL, s.EMAIL_SUBJECT_PREFIX.strip())\n"
    )
    done = subprocess.run(  # noqa: S603 - مفسر همین آزمون و کد ثابت
        [sys.executable, "-c", code], cwd=SRC, env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    mailer, sender = done.stdout.splitlines()
    assert mailer.split() == ["EmailBackend", "localhost", "587", "True", "10", "0"]
    # فرستنده از دامنهٔ خود سایت (SPF/DKIM همان دامنه)، نه یک دامنهٔ ثابت بیگانه.
    assert sender.split() == ["no-reply@gam-khedmat.ir", "no-reply@gam-khedmat.ir", "[مهدیه]"]
