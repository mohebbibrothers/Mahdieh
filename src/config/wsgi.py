"""نقطهٔ ورود WSGI — همان چیزی که گونیکورن با ``-k sync`` اجرا می‌کند.

چیدمان لایه‌ها (از بیرون به داخل):

    WSGIConcurrencyLimitMiddleware ⇒ سدِ ظرفیتِ **مشترک** (اختیاری، پیش‌فرض خاموش)
    Django WSGI application        ⇒ میان‌افزارها، نماها، قاعدهٔ دامنه

چرا سدِ WSGI پیش‌فرض خاموش است؟ با کارگر ``sync`` هر فرآیند فقط یک درخواست را
هم‌زمان اجرا می‌کند؛ پس سقفِ مؤثرِ شمارنده برابر شمارِ کارگرهاست و فعال‌کردن آن
باعث می‌شود در اوج بار، درخواستِ سالمی که ظرفیتش آزاد شده هم دور ریخته شود.
سدِ این مدل، **Nginx** است (``limit_conn``/``limit_req``). اگر روزی کارگرِ
رشته‌ای انتخاب شد، ``LOAD_SHEDDING_WSGI_ENABLED=true`` همین لایه را روشن
می‌کند. دلیل کامل: ``docs/adr/0016-shedding-at-the-edge-for-sync-wsgi.md``.
"""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

django_application = get_wsgi_application()


def _build_application() -> object:
    """ساخت اپ؛ سد فقط اگر هم‌زمانِ عمومی و هم‌زمانِ WSGI روشن باشند."""
    from django.conf import settings

    from apps.core.shedding import WSGIConcurrencyLimitMiddleware

    if not (settings.LOAD_SHEDDING_ENABLED and settings.LOAD_SHEDDING_WSGI_ENABLED):
        return django_application
    return WSGIConcurrencyLimitMiddleware(
        django_application, limit=settings.LOAD_SHEDDING_MAX_CONCURRENCY
    )


application = _build_application()
