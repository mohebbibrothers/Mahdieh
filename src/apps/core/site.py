"""نشانی عمومی سایت — تنها جایی که «لینک مطلق» ساخته می‌شود.

چرا ماژول جدا؟ چند جا به نشانی مطلق نیاز دارند و هیچ‌کدام در دسترس درخواست
نیستند (پیامک، گواهی چاپی، گزارش اکسل). اگر هر ماژول خودش رشته بسازد، دیر یا
زود یکی «//verify» یا «http://127.0.0.1» در تولید چاپ می‌کند. پس یک تابع،
یک قاعده:

    • در نمای وب، نشانی همان درخواست اولویت دارد (پشت پراکسی هم درست می‌ماند).
    • در کارهای بدون درخواست، از تنظیم ``SITE_URL`` خوانده می‌شود.
    • هیچ‌گاه اسلش انتهایی اضافه نمی‌ماند.
"""

from __future__ import annotations


def site_base_url(base_url: str | None = None) -> str:
    """نشانی پایهٔ سایت بدون اسلش انتهایی."""
    from django.conf import settings

    candidate = base_url or str(getattr(settings, "SITE_URL", "") or "")
    if not candidate:
        candidate = "http://127.0.0.1:8000"
    return candidate.rstrip("/")


def absolute_url(path: str, *, base_url: str | None = None) -> str:
    """ساخت نشانی مطلق از یک مسیر نسبی (``/verify/<token>/``)."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{site_base_url(base_url)}{normalized}"
