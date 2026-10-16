"""نقطهٔ ورود ASGI — مسیر اصلی اجرا در تولید (HTTP + بلادرنگ).

چیدمان لایه‌ها (از بیرون به داخل):
    ConcurrencyLimitMiddleware ⇒ سدِ ظرفیت: بار مازاد در درِ ورودی با ۵۰۳ برمی‌گردد
    Django ASGI application    ⇒ میان‌افزارها، نماها، و قاعدهٔ دامنه

ترتیب مهم است: سد باید **بیرونِ** جنگو بنشیند تا پیش از اشغال رشته و اتصالِ
پایگاه‌داده تصمیم بگیرد. مسیرهای سلامت و دارایی ایستا از سد معاف‌اند تا پایشگر
هرگز با ۵۰۳ روبه‌رو نشود و CSS در فشار هم بارگذاری شود.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

django_application = get_asgi_application()


def _build_application() -> object:
    """ساخت اپ با تنظیمات بارگیری‌شده (تا import زمان‌بندی‌شده باشد)."""
    from django.conf import settings

    from apps.core.shedding import ConcurrencyLimitMiddleware

    if not settings.LOAD_SHEDDING_ENABLED:
        return django_application
    return ConcurrencyLimitMiddleware(
        django_application, max_concurrency=settings.LOAD_SHEDDING_MAX_CONCURRENCY
    )


application = _build_application()
