"""مدل‌های پایه — همهٔ مدل‌های دامنه از این‌ها ارث می‌برند.

قواعد:
    • کلید اصلی UUIDv7 (غیرقابل‌حدس، مرتب با زمان).
    • ``created_at``/``updated_at`` هرگز دستی پر نمی‌شوند.
    • حذف نرم پیش‌فرض است: سابقهٔ رزرو هرگز فیزیکی حذف نمی‌شود.
    • ``version`` برای قفل خوش‌بینانه در عملیات هم‌زمان مدیریتی.
"""

from __future__ import annotations

from typing import Any, TypeVar

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.uuid7 import uuid7

_M = TypeVar("_M", bound=models.Model)


class SoftDeleteQuerySet(models.QuerySet[_M]):
    """QuerySet با پشتیبانی حذف نرم و بازیابی.

    جنریک است تا django-stubs بتواند برای هر مدل، نوع درست را استنتاج کند و
    ``all_objects.filter(...)`` روی فیلدهای همان مدل، خطای نوع ندهد.
    """

    def alive(self) -> SoftDeleteQuerySet[_M]:
        """فقط رکوردهای حذف‌نشده."""
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet[_M]:
        """فقط رکوردهای حذف‌شده (برای بازیابی و ممیزی)."""
        return self.filter(deleted_at__isnull=False)

    def delete(self) -> int:  # type: ignore[override]
        """حذف نرم دسته‌ای — بازگشت تعداد رکوردهای اثرپذیر."""
        return self.update(deleted_at=timezone.now())

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """حذف فیزیکی — فقط برای تست و پاک‌سازی کنترل‌شده."""
        return super().delete()

    def restore(self) -> int:
        """بازگرداندن رکوردهای حذف‌شده."""
        return self.update(deleted_at=None)


class SoftDeleteManager(models.Manager[_M]):
    """مدیر پیش‌فرض: همیشه فقط رکوردهای زنده را می‌بیند."""

    def get_queryset(self) -> SoftDeleteQuerySet[_M]:
        """QuerySet پیش‌فرض محدود به رکوردهای زنده."""
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class AllObjectsManager(models.Manager[_M]):
    """مدیر کامل — برای ممیزی، گزارش و بازیابی."""

    def get_queryset(self) -> SoftDeleteQuerySet[_M]:
        """QuerySet بدون فیلتر حذف."""
        return SoftDeleteQuerySet(self.model, using=self._db)


class BaseModel(models.Model):
    """مدل پایهٔ همهٔ موجودیت‌های مهدیه."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid7,
        editable=False,
        verbose_name=_("شناسه"),
        help_text=_("شناسهٔ UUIDv7 — مرتب با زمان و غیرقابل‌حدس"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name=_("زمان ایجاد")
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("زمان ویرایش"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("فعال"))
    deleted_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name=_("زمان حذف نرم")
    )

    # بدون پارامتر صریح نوشته می‌شوند تا django-stubs آن‌ها را برای هر مدل
    # فرزند «تخصصی» کند (سپس all_objects.filter(sequence=...) خطای نوع نمی‌دهد).
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True
        get_latest_by = "created_at"

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        """حذف نرم رکورد (پیش‌فرض امن).

        پارامترها بخشی از امضای جنگو هستند و در حذف نرم استفاده نمی‌شوند.
        """
        del using, keep_parents
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])
        return (1, {self._meta.label: 1})

    def hard_delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        """حذف فیزیکی — استفادهٔ آن باید در حسابرسی توجیه داشته باشد."""
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self) -> None:
        """بازگرداندن رکورد حذف‌شده."""
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])


class VersionedModel(BaseModel):
    """افزودن قفل خوش‌بینانه برای عملیات هم‌زمان مدیریتی.

    سناریو: دو کارمند تابلوی زمان‌بندی را هم‌زمان باز می‌کنند و هر دو یک رزرو
    را جابه‌جا می‌کنند. با ``version``، دومین تلاش ۴۰۹ می‌گیرد و از بازنویسی
    خاموش جلوگیری می‌شود.
    """

    version = models.PositiveIntegerField(default=1, verbose_name=_("نسخه"))

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """افزایش خودکار نسخه در هر ذخیره‌سازی."""
        if not self._state.adding:
            self.version += 1
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "version"}
            else:
                kwargs.setdefault("update_fields", None)
        super().save(*args, **kwargs)

    def bump_version_guard(self) -> dict[str, int]:
        """شرط قفل برای ``queryset.filter(**guard)``."""
        return {"version": self.version}


class SerialLock(models.Model):
    """ردیف‌های قفل نام‌دار — سریال‌سازی تراکنشی بخش‌های «مقدار بعدی» (ADR-0020).

    ``SELECT … FOR UPDATE`` روی «آخرین ردیف» در InnoDB سریال‌سازی نمی‌کند: پرس‌وجوی
    منتظر پس از آزادشدن قفل، ردیف تازه‌درج‌شده را نمی‌بیند. یک ردیف ثابتِ
    نام‌دار که با ``INSERT … ON DUPLICATE KEY UPDATE`` (ساخت + قفل انحصاری در یک
    دستور) گرفته می‌شود، این خلأ را ندارد. استفاده: :func:`apps.core.db.serial.serialize_on`.
    """

    name = models.CharField(max_length=64, primary_key=True, verbose_name=_("نام"))

    class Meta:
        db_table = "core_serial_lock"
        verbose_name = _("قفل نام‌دار")
        verbose_name_plural = _("قفل‌های نام‌دار")

    def __str__(self) -> str:
        """نمای خوانا."""
        return self.name
