"""نقطهٔ ورود Passenger روی میزبان cPanel — پوستهٔ نازکِ «انتشار اتمی».

چیدمان روی میزبان (``~/mahdieh``)::

    app/passenger_wsgi.py     ← همین فایل (ریشهٔ برنامهٔ Passenger؛ ثابت)
    app/tmp/restart.txt       ← لمسش = بازنشانی نرم فرآیندها
    current -> releases/<id>  ← تنها چیزی که استقرار عوض می‌کند (اتمی)
    releases/<id>/.venv -> ../../venvs/<key>

هر فرآیندِ تازه یک بار ``current`` را **حل می‌کند** و تا پایان عمرش به همان
انتشار پایبند می‌ماند؛ پس هیچ درخواستی نیمی از کد قدیم و نیمی از کد جدید را
نمی‌بیند. مفسرِ Passenger (محیط مجازی cloudlinux-selector) همان alt-python 3.12
است؛ بسته‌های انتشار از محیط مجازیِ خودِ انتشار به ابتدای ``sys.path`` می‌آیند.

همین فایل را ``wsgi_probe.py`` هم پیش از جابه‌جایی ``current`` بارگذاری می‌کند
تا انتشار تازه را **درون‌فرآیندی** و دقیقاً با همین منطق بیازماید.
"""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import site
import sys
from typing import Any


def mahdieh_home() -> Path:
    """ریشهٔ استقرار: ``MAHDIEH_HOME`` یا ``~/mahdieh`` کاربر جاری (بی‌اتکا به HOME)."""
    explicit = os.environ.get("MAHDIEH_HOME", "").strip()
    if explicit:
        return Path(explicit)
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "mahdieh"


def release_site_packages(release: Path) -> Path:
    """مسیر site-packages محیط مجازیِ انتشار برای همین نسخهٔ مفسر."""
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    return release / ".venv" / "lib" / version / "site-packages"


def activate(release: Path) -> Path:
    """آماده‌سازی ``sys.path`` برای یک انتشار مشخص؛ مسیر حل‌شدهٔ انتشار را برمی‌گرداند."""
    resolved = release.resolve(strict=True)
    packages = release_site_packages(resolved)
    if not packages.is_dir():
        msg = (
            f"محیط مجازی انتشار برای مفسر {sys.version_info[0]}.{sys.version_info[1]} "
            f"یافت نشد: {packages}"
        )
        raise RuntimeError(msg)
    before = list(sys.path)
    site.addsitedir(str(packages))  # پرونده‌های .pth هم پردازش می‌شوند
    added = [entry for entry in sys.path if entry not in before]
    source = str(resolved / "src")
    rest = [entry for entry in before if entry != source]
    sys.path[:] = [source, *added, *rest]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
    os.environ["MAHDIEH_RELEASE_DIR"] = str(resolved)
    return resolved


def load_application(release: Path) -> Any:
    """بارگذاری اپ WSGI جنگو از یک انتشار."""
    activate(release)
    from config.wsgi import application

    return application


if os.environ.get("MAHDIEH_WSGI_NO_AUTOLOAD") != "1":
    application = load_application(mahdieh_home() / "current")
