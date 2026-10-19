"""تست بهداشت تنظیمات — گرفتن «تنظیمات مرده» پیش از آنکه در تولید فریاد بزنند.

ماجرای واقعی این تست: در فاز ۷، دو تنظیم به‌صورت **متغیر ماژولی** نوشته شده بود
(``STATICFILES_STORAGE`` و ``CONN_MAX_AGE``). جنگو هیچ‌کدام را از آن‌جا نمی‌خواند؛
نتیجه: دارایی ایستا بدون نسخه‌بندی سرو می‌شد و عمر اتصال پایگاه‌داده بی‌اثر
می‌ماند. هیچ تستی نمی‌گرفت، چون «تنظیم وجود دارد» به‌نظر می‌رسید.

این فایل همان کلاس خطا را می‌بندد: هر نامی که در جنگو ۶ یا حذف شده یا جای
دیگری خوانده می‌شود، اگر در یک ماژول تنظیمات تعریف شود، تست شکست می‌خورد.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from django.conf import settings

SETTINGS_DIR: Final[Path] = Path(settings.BASE_DIR) / "config" / "settings"

# نام‌هایی که در Django 6.x یا حذف شده‌اند یا تنها در جای دیگری خوانده می‌شوند.
# هر مورد، درسِ یک باگ واقعی است.
DEAD_SETTINGS: Final[dict[str, str]] = {
    "STATICFILES_STORAGE": "در Django 6 حذف شد ⇒ STORAGES['staticfiles']['BACKEND']",
    "DEFAULT_FILE_STORAGE": "در Django 6 حذف شد ⇒ STORAGES['default']['BACKEND']",
    "CONN_MAX_AGE": "تنها داخل DATABASES['default'] خوانده می‌شود، نه به‌صورت متغیر ماژولی",
    "CONN_HEALTH_CHECKS": "تنها داخل DATABASES['default'] خوانده می‌شود",
    "USE_L10N": "در Django 5 حذف شد؛ فارسی‌سازی خودکار است",
    "CSRF_COOKIE_MASKED": "بی‌اثر و گمراه‌کننده است",
    "PASSWORD_RESET_TIMEOUT_DAYS": "در Django 4 حذف شد ⇒ PASSWORD_RESET_TIMEOUT (ثانیه)",
}

# ماژول‌هایی که به‌طور عمدی «قاعده را می‌شکنند» و دلیلش را نوشته‌اند.
EXEMPT: Final[frozenset[str]] = frozenset()


def _assignments(path: Path) -> list[tuple[str, int]]:
    """نام‌های سطح-ماژولِ تخصیص‌داده‌شده در یک فایل تنظیمات."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.append((target.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.append((node.target.id, node.lineno))
    return found


def test_no_dead_settings_in_any_settings_module() -> None:
    """هیچ ماژول تنظیماتی نباید نام تنظیماتِ حذف‌شده/جابه‌جاشده را تعریف کند."""
    violations: list[str] = []
    for path in sorted(SETTINGS_DIR.glob("*.py")):
        if path.name in EXEMPT:
            continue
        for name, lineno in _assignments(path):
            reason = DEAD_SETTINGS.get(name)
            if reason:
                violations.append(f"{path.name}:{lineno} — {name} ({reason})")
    assert not violations, "تنظیمات مرده: " + " | ".join(violations)


def test_dead_setting_detector_actually_catches_the_bug(tmp_path: Path) -> None:
    """خودِ قاعده آزموده می‌شود: تنظیم مرده را می‌گیرد و مثبت کاذب نمی‌سازد.

    بدون این تست، ممکن بود «قاعده‌ای که هیچ‌چیز نمی‌گیرد» داشته باشیم و خیال
    کنیم محافظت شده‌ایم.
    """
    offender = tmp_path / "bad.py"
    offender.write_text("CONN_MAX_AGE = 600\n", encoding="utf-8")
    innocent = tmp_path / "good.py"
    innocent.write_text(
        'DATABASES["default"]["CONN_MAX_AGE"] = 0\nSECURE_HSTS_SECONDS = 31_536_000\n',
        encoding="utf-8",
    )
    dead_names = {name for name, _line in _assignments(offender)}
    safe_names = {name for name, _line in _assignments(innocent)}
    assert dead_names & set(DEAD_SETTINGS), "باید تنظیم مرده را بگیرد"
    assert not (safe_names & set(DEAD_SETTINGS)), "نباید برای تنظیم درست مثبت کاذب بدهد"


def test_database_connection_lifetime_lives_inside_databases() -> None:
    """عمر اتصال باید داخل ``DATABASES`` باشد؛ بیرونش بی‌اثر است."""
    assert "CONN_MAX_AGE" in settings.DATABASES["default"]
    assert settings.DATABASES["default"]["CONN_HEALTH_CHECKS"] is True


def test_every_database_alias_shares_the_same_server_and_options() -> None:
    """اتصال کش آینهٔ ``default`` است: همان سرور، همان پایگاه‌داده، همان گزینه‌ها.

    اگر روزی کسی ``DATABASE_URL`` را عوض کند و نام ``cache`` جا بماند، کش به
    پایگاه‌دادهٔ دیگری می‌نوشت و ابطال تقویم بی‌صدا از کار می‌افتاد.
    """
    default = settings.DATABASES["default"]
    cache_alias = settings.DATABASES["cache"]
    for key in ("ENGINE", "HOST", "PORT", "USER", "OPTIONS"):
        assert cache_alias[key] == default[key], key
    assert cache_alias["TEST"]["MIRROR"] == "default"


# یادداشت: پوشش «انبار دارایی نسخهدار در تولید» و «پرچمهای سختسازی» عمداً در
# src/tests/test_deployment_contract.py است (با فیکسچر تولید) — اینجا تکرار
# نمیشود. دلیل: import مستقیم ماژول تنظیمات تولید در همین فرایند، به ترتیب
# اجرای تستها وابسته میشد (کش ماژول). یک قاعده، یک خانه.
