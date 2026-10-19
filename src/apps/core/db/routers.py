"""مسیریاب پایگاه‌داده — کش روی اتصال جداگانه، باقی روی ``default``.

جدول کش (``django_cache``) در همان پایگاه‌داده است ولی از اتصالی جدا خوانده و
نوشته می‌شود تا نوشتن در کش هرگز در تراکنش کسب‌وکار گیر نکند و با برگشت آن هم
برنگردد (ر.ک. ``apps.core.db.cache``). در آزمون‌ها این اتصال آینهٔ ``default``
است (``TEST.MIRROR``) تا جداسازی آزمون‌ها حفظ شود.
"""

from __future__ import annotations

from typing import Any

CACHE_ALIAS = "cache"
CACHE_APP_LABEL = "django_cache"


class CacheRouter:
    """ارسال خواندن/نوشتن کش به اتصال ``cache``؛ مدل‌های برنامه دست نمی‌خورند."""

    def db_for_read(self, model: type[Any], **hints: Any) -> str | None:
        """خواندن کش ⇒ اتصال کش."""
        del hints
        return CACHE_ALIAS if model._meta.app_label == CACHE_APP_LABEL else None

    def db_for_write(self, model: type[Any], **hints: Any) -> str | None:
        """نوشتن کش ⇒ اتصال کش."""
        del hints
        return CACHE_ALIAS if model._meta.app_label == CACHE_APP_LABEL else None

    def allow_migrate(
        self, db: str, app_label: str, model_name: str | None = None, **hints: Any
    ) -> bool | None:
        """مهاجرت مدل‌ها فقط روی ``default``؛ اتصال کش هیچ جدول دیگری نمی‌سازد.

        جدول کش روی هر دو نام مجاز است چون هر دو به **یک** پایگاه‌داده اشاره
        می‌کنند و ``createcachetable`` در ساخت پایگاه‌دادهٔ آزمون روی ``default``
        اجرا می‌شود.
        """
        del model_name, hints
        if app_label == CACHE_APP_LABEL:
            return True
        if db == CACHE_ALIAS:
            return False
        return None
