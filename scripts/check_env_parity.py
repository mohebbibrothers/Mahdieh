#!/usr/bin/env python
"""بررسی هم‌خوانی نمونه‌های محیط با تنظیمات — «سند کهنه، شکست CI است».

سه چیز را می‌سنجد:

۱. ``.env.example`` (توسعه): هر متغیری که تنظیمات می‌خواند، مستند شده باشد و
   هرچه در نمونه است واقعاً خوانده شود.
۲. ``deploy/env/mahdieh.env.example`` (تولید): هیچ نامی در آن نباشد که جایی
   خوانده نمی‌شود (گارد غلط‌تایپی) و متغیرهای الزامی/امنیتی را داشته باشد. این‌جا
   بود که یک اشتباه واقعی لو رفت: نام متغیر ``ENVIRONMENT=production`` نوشته شده
   بود ولی تنظیمات ``MAHDIEH_ENVIRONMENT`` می‌خواند؛ یعنی روی سرور تولید، سامانه
   فکر می‌کرد در «توسعه» است و هشدارهای مخصوص تولید خاموش می‌ماندند.
۳. متغیرهای **الزامی** تولید، که نبودشان استقرار را متوقف می‌کند.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
DEPLOY_EXAMPLE = ROOT / "deploy" / "env" / "mahdieh.env.example"
SETTINGS = ROOT / "src" / "config" / "settings"

# متغیرهایی که در نمونهٔ توسعه هستند ولی در تنظیمات خوانده نمی‌شوند (تحمل کنترل‌شده)
ALLOWED_EXTRA = {"MAHDIEH_ADMINS"}

#: متغیرهایی که **ابزار اجرا** می‌خواند، نه جنگو (با دلیل، تا کسی سرگردان نشود).
RUNTIME_ONLY = {
    "DJANGO_SETTINGS_MODULE": "خودِ manage.py و ابزارهای پایتون می‌خوانند.",
    "WEB_CONCURRENCY": "دستور gunicorn در Dockerfile/systemd می‌خواند (شمار کارگر).",
}

#: متغیرهایی که **وضعیت امنیتی/عملیاتی** را عوض می‌کنند: باید یا در نمونهٔ تولید
#: مستند باشند یا در ``production.py`` قفل شده باشند — «هیچ‌کدام» یعنی ابهام.
SECURITY_RELEVANT = {
    "MAHDIEH_DEBUG",
    "SECURE_SSL_REDIRECT",
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "SECURE_HSTS_SECONDS",
    "CSP_REPORT_ONLY",
    "HEALTH_DETAILED_PUBLIC",
    "OTP_MAX_ATTEMPTS",
    "OTP_WINDOW_SECONDS",
    "EXPORT_MAX_ROWS",
    "STORAGE_BACKEND",
    "MAHDIEH_CORS_ORIGINS",
    "MAHDIEH_CROSS_SITE_COOKIES",
    "SMS_ENABLED",
}

#: متغیرهایی که فایل محیط تولید **باید** داشته باشد (بی‌آن‌ها استقرار معنا ندارد).
REQUIRED_IN_DEPLOY = {
    "DJANGO_SETTINGS_MODULE",
    "MAHDIEH_ENVIRONMENT",
    "MAHDIEH_SECRET_KEY",
    "MAHDIEH_ALLOWED_HOSTS",
    "MAHDIEH_SITE_URL",
    "MAHDIEH_SUPPORT_ADDRESS",
    "DATABASE_URL",
    "VALKEY_URL",
    "FIELD_ENCRYPTION_KEY",
    "FIELD_HASH_KEY",
    "SMS_ENABLED",
    "SMS_PATTERN_CODES",
}


def declared_in(path: Path) -> set[str]:
    """نام متغیرهای تعریف‌شده در یک فایل نمونه (خطوط ``NAME=``)."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))


#: خطی مثل ``MAHDIEH_SECRET_KEY=   `` (فقط فاصله) از نظر تنظیمات «مقدار دارد».
#: آزمون واقعی نشان داد چنین خطی مهلک است: نه خطای روشن می‌دهد و نه کار می‌کند؛
#: فقط در میانهٔ استقرار، خطای احراز هویت یا راز نامعتبر می‌سازد.
SPACES_ONLY = re.compile(r"^([A-Z][A-Z0-9_]+)=[ \t]+$", flags=re.MULTILINE)


def read_in_settings() -> set[str]:
    """نام متغیرهایی که تنظیمات (یا کد) آن‌ها را می‌خواند."""
    names: set[str] = set()
    for path in SETTINGS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"""env\.\w+\(\s*["']([A-Z][A-Z0-9_]+)["']""", text))
    return names


def compare(*, path: Path, used: set[str], label: str, complete: bool) -> list[str]:
    """مقایسهٔ یک نمونهٔ محیط با تنظیمات؛ بازگرداندن فهرست ایرادها.

    ``complete=True`` (نمونهٔ توسعهٔ محلی): هر متغیر خوانده‌شده باید مستند باشد.
    ``complete=False`` (نمونهٔ تولید): تنها نامِ **بی‌مصرف** ایراد است؛ توضیح
    همهٔ ۷۷ متغیر در فایل تولید، فایل را ناخوانا می‌کند و رازها را شلوغ می‌کند.
    """
    declared = declared_in(path)
    if not declared:
        return [f"{label}: هیچ متغیری در {path.name} پیدا نشد (فایل خالی یا خراب است؟)"]
    problems: list[str] = []
    unread = sorted((declared - used) - set(RUNTIME_ONLY))
    if unread:
        problems.append(f"{label}: نامی که هیچ‌جا خوانده نمی‌شود (احتمال غلط‌تایپی) → {unread}")
    if complete:
        undocumented = sorted((used - declared) - ALLOWED_EXTRA)
        if undocumented:
            problems.append(f"{label}: خوانده می‌شود ولی مستند نشده → {undocumented}")
    return problems


def main() -> int:
    """اجرای همهٔ بررسی‌ها."""
    used = read_in_settings()
    problems: list[str] = []
    problems += compare(path=EXAMPLE, used=used, label="نمونهٔ توسعه", complete=True)
    problems += compare(path=DEPLOY_EXAMPLE, used=used, label="نمونهٔ تولید", complete=False)

    missing_required = sorted(REQUIRED_IN_DEPLOY - declared_in(DEPLOY_EXAMPLE))
    if missing_required:
        problems.append(f"نمونهٔ تولید این متغیرهای الزامی را ندارد → {missing_required}")

    # خط‌های «فقط فاصله»: ظاهرشان پر است، باطنشان خالی — تلهٔ استقرار.
    for path, label in ((EXAMPLE, "نمونهٔ توسعه"), (DEPLOY_EXAMPLE, "نمونهٔ تولید")):
        trapped = sorted(SPACES_ONLY.findall(path.read_text(encoding="utf-8")))
        if trapped:
            problems.append(
                f"{label}: مقدار «فقط فاصله» دارد (تهی به‌حساب می‌آید، ولی خوانا نیست) → {trapped}"
            )

    # قاعدهٔ نام‌گذاری: نام محیط همان است که تنظیمات می‌خواند.
    if "ENVIRONMENT" in declared_in(DEPLOY_EXAMPLE):
        problems.append("نمونهٔ تولید «ENVIRONMENT» را دارد؛ نام درست MAHDIEH_ENVIRONMENT است.")

    # متغیرهای امنیتی: یا مستند، یا در تنظیمات تولید قفل‌شده.
    production_text = (SETTINGS / "production.py").read_text(encoding="utf-8")
    ambiguous = sorted(
        name
        for name in SECURITY_RELEVANT
        if name not in declared_in(DEPLOY_EXAMPLE) and name not in production_text
    )
    if ambiguous:
        problems.append(
            f"متغیرهای امنیتی بدون جای مشخص (نه در نمونهٔ تولید، نه در production.py) → {ambiguous}"
        )

    if problems:
        print("❌ هم‌خوانی محیط برقرار نیست:")
        for problem in problems:
            print(f"   • {problem}")
        return 1
    print(
        f"✅ هم‌خوانی محیط برقرار است ({len(used)} متغیر مستند · "
        f"{len(declared_in(DEPLOY_EXAMPLE))} متغیر در نمونهٔ تولید)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
