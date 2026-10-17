"""مدل‌های موجودی — ۳ طبقه، ۵ واحد، امکانات، مناسبت‌ها و بلوک‌ها.

قواعد ثابت این دامنه:
    • **۵ واحد واقعی**: طبقهٔ ۱ (سوئیت + اتاق بزرگ)، طبقهٔ ۲ (سوئیت + اتاق
      بزرگ)، طبقهٔ ۳ (اتاق بزرگ). هیچ واحدی خارج از این ساختار معنا ندارد.
    • **هیچ‌گاه حذف فیزیکی**: بلوک و واحدها حذف نرم می‌شوند تا سابقهٔ رزروها
      (که به آن‌ها ارجاع دارند) سالم بماند.
    • **قید دیتابیسی روی بلوک‌ها**: دو بلوک هم‌پوشان روی یک واحد ممنوع است؛
      «تقویم متناقض» از نظر فیزیکی ناممکن می‌شود.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.jalali import format_jalali
from apps.core.models import BaseModel
from apps.inventory.choices import BlockKind, DemandLevel, OccasionKind, UnitType

UNIT_CODE_MAX_LENGTH = 8
REASON_MIN_LENGTH = 5


class Floor(BaseModel):
    """طبقه — سطح فیزیکی ساختمان (۱ تا ۳)."""

    number = models.PositiveSmallIntegerField(unique=True, verbose_name=_("شمارهٔ طبقه"))
    title = models.CharField(max_length=60, verbose_name=_("عنوان"))
    description = models.TextField(blank=True, default="", verbose_name=_("توضیح"))
    accessibility_notes = models.CharField(
        max_length=200, blank=True, default="", verbose_name=_("نکات دسترسی")
    )
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("ترتیب نمایش"))

    class Meta:
        verbose_name = _("طبقه")
        verbose_name_plural = _("طبقات")
        ordering = ("sort_order", "number")

    def __str__(self) -> str:
        """نمای خوانا برای پنل مدیریت."""
        return f"{self.title} (طبقهٔ {self.number})"


class UnitFeature(BaseModel):
    """امکان یک واحد — «وای‌فای»، «پارکینگ»، «سرویس اختصاصی» و …."""

    code = models.SlugField(max_length=40, unique=True, verbose_name=_("کد"))
    title = models.CharField(max_length=60, verbose_name=_("عنوان"))
    icon = models.CharField(max_length=8, blank=True, default="", verbose_name=_("نماد"))
    is_highlight = models.BooleanField(default=False, verbose_name=_("امکان شاخص"))
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("ترتیب نمایش"))

    class Meta:
        verbose_name = _("امکان")
        verbose_name_plural = _("امکانات")
        ordering = ("-is_highlight", "sort_order", "title")

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"{self.title}"


class Unit(BaseModel):
    """واحد اقامتی — قابل‌رزروترین موجودیت فیزیکی زائرسرا."""

    code = models.CharField(
        max_length=UNIT_CODE_MAX_LENGTH,
        unique=True,
        verbose_name=_("کد واحد"),
        help_text=_("کد کوتاه برای مکالمهٔ تلفنی و تابلو؛ مثل A1"),
    )
    floor = models.ForeignKey(
        Floor, on_delete=models.PROTECT, related_name="units", verbose_name=_("طبقه")
    )
    unit_type = models.CharField(
        max_length=16, choices=UnitType.choices, db_index=True, verbose_name=_("نوع واحد")
    )
    title = models.CharField(max_length=80, verbose_name=_("عنوان"))
    capacity_adults = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(1)], verbose_name=_("ظرفیت بزرگسال")
    )
    capacity_children = models.PositiveSmallIntegerField(default=2, verbose_name=_("ظرفیت کودک"))
    cleaning_buffer_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=None,
        verbose_name=_("بافر نظافت (دقیقه)"),
        help_text=_("خالی = تابع سیاست سراسری «بافر نظافت»؛ صفر = صریحاً بدون بافر."),
    )
    features = models.ManyToManyField(
        UnitFeature,
        through="UnitFeatureLink",
        related_name="units",
        blank=True,
        verbose_name=_("امکانات"),
    )
    notes = models.TextField(blank=True, default="", verbose_name=_("یادداشت کارکنان"))
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("ترتیب نمایش"))
    deactivated_reason = models.CharField(
        max_length=200, blank=True, default="", verbose_name=_("دلیل غیرفعال‌سازی")
    )
    deactivated_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان غیرفعال‌سازی"))

    class Meta:
        verbose_name = _("واحد اقامتی")
        verbose_name_plural = _("واحدهای اقامتی")
        ordering = ("floor__sort_order", "floor__number", "sort_order", "code")
        indexes = [
            models.Index(fields=["unit_type", "is_active"], name="unit_type_active_idx"),
            models.Index(fields=["floor", "sort_order"], name="unit_floor_sort_idx"),
        ]

    def __str__(self) -> str:
        """نمای خوانا برای پنل و پیامک."""
        return f"{self.title} ({self.code})"

    @property
    def capacity_total(self) -> int:
        """ظرفیت کل واحد (بزرگسال + کودک)."""
        return self.capacity_adults + self.capacity_children

    @property
    def floor_number(self) -> int:
        """شمارهٔ طبقه — برای نمایش سریع در تقویم و گزارش."""
        return int(self.floor.number)

    @property
    def type_label(self) -> str:
        """برچسب فارسی نوع واحد."""
        return str(UnitType(self.unit_type).label)

    @property
    def is_bookable(self) -> bool:
        """آیا این واحد در چرخهٔ رزرو است؟"""
        return self.is_active and self.deleted_at is None


class UnitFeatureLink(models.Model):
    """پیوند واحد و امکان — با یادداشت اختیاری («۲ دستگاه کولر»)."""

    unit = models.ForeignKey(
        Unit, on_delete=models.CASCADE, related_name="feature_links", verbose_name=_("واحد")
    )
    feature = models.ForeignKey(
        UnitFeature,
        on_delete=models.CASCADE,
        related_name="feature_links",
        verbose_name=_("امکان"),
    )
    note = models.CharField(max_length=120, blank=True, default="", verbose_name=_("یادداشت"))

    class Meta:
        verbose_name = _("پیوند امکان")
        verbose_name_plural = _("پیوندهای امکانات")
        constraints = [
            models.UniqueConstraint(fields=["unit", "feature"], name="uniq_unit_feature"),
        ]

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"{self.unit.code} → {self.feature.title}"


class UnitPhoto(models.Model):
    """تصویر واحد — پوشانده‌شده در گالری تقویم."""

    unit = models.ForeignKey(
        Unit, on_delete=models.CASCADE, related_name="photos", verbose_name=_("واحد")
    )
    image = models.FileField(upload_to="units/photos/", verbose_name=_("تصویر"))
    alt_text = models.CharField(
        max_length=160, blank=True, default="", verbose_name=_("متن جایگزین")
    )
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("ترتیب"))
    is_cover = models.BooleanField(default=False, verbose_name=_("تصویر شاخص"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))

    class Meta:
        verbose_name = _("تصویر واحد")
        verbose_name_plural = _("تصاویر واحدها")
        ordering = ("-is_cover", "sort_order", "created_at")

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"تصویر {self.unit.code}"


class Occasion(BaseModel):
    """مناسبت — تعطیل رسمی، مذهبی یا اوج فصلی که تقاضا را تغییر می‌دهد."""

    title = models.CharField(max_length=80, verbose_name=_("عنوان"))
    kind = models.CharField(
        max_length=16, choices=OccasionKind.choices, db_index=True, verbose_name=_("نوع")
    )
    date_gregorian = models.DateField(db_index=True, verbose_name=_("تاریخ میلادی"))
    date_jalali = models.CharField(
        max_length=12, blank=True, default="", verbose_name=_("تاریخ جلالی")
    )
    demand_level = models.CharField(
        max_length=8,
        choices=DemandLevel.choices,
        default=DemandLevel.NORMAL,
        db_index=True,
        verbose_name=_("سطح تقاضا"),
    )
    is_fixed_every_year = models.BooleanField(
        default=False,
        verbose_name=_("هر سال ثابت"),
        help_text=_("مثل نوروز؛ مناسبت‌های قمری ثابت نیستند"),
    )
    rule = models.JSONField(
        default=dict, blank=True, verbose_name=_("قاعدهٔ تکرار"), help_text=_("برای مناسبت‌های متغیر")
    )
    description = models.TextField(blank=True, default="", verbose_name=_("توضیح"))

    class Meta:
        verbose_name = _("مناسبت")
        verbose_name_plural = _("مناسبت‌ها")
        ordering = ("date_gregorian", "title")
        indexes = [
            models.Index(
                fields=["date_gregorian", "demand_level"], name="occasion_date_demand_idx"
            ),
        ]

    def __str__(self) -> str:
        """نمای خوانا با تاریخ جلالی."""
        return f"{self.title} — {self.date_jalali or self.date_gregorian}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """پرکردن خودکار تاریخ جلالی از تاریخ میلادی (ثبات نمایش دوزبانه)."""
        if self.date_gregorian and not self.date_jalali:
            self.date_jalali = format_jalali(self.date_gregorian, numeric=True)
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "date_jalali"}
        super().save(*args, **kwargs)

    @property
    def is_peak(self) -> bool:
        """آیا این مناسبت در اوج تقاضا است؟"""
        return self.demand_level == DemandLevel.PEAK


class Block(BaseModel):
    """بلوک نگهداری — بازه‌ای که رزرو در آن ناممکن است.

    ``unit`` تهی ⇒ بلوک سراسری (کل زائرسرا). برای بلوک‌های واحد‌محور، قید
    ``EXCLUDE`` دیتابیس اجازهٔ دو بلوک هم‌پوشان نمی‌دهد.
    """

    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="blocks",
        verbose_name=_("واحد"),
        help_text=_("تهی ⇒ بلوک سراسری برای همهٔ واحدها"),
    )
    period = DateTimeRangeField(verbose_name=_("بازهٔ بلوک"))
    kind = models.CharField(
        max_length=16, choices=BlockKind.choices, db_index=True, verbose_name=_("نوع بلوک")
    )
    reason = models.CharField(
        max_length=300,
        verbose_name=_("دلیل"),
        help_text=_("اجباری؛ در تقویم عمومی به‌صورت برچسب نشان داده می‌شود."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blocks_created",
        verbose_name=_("ایجادکننده"),
    )

    class Meta:
        verbose_name = _("بلوک نگهداری")
        verbose_name_plural = _("بلوک‌های نگهداری")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["unit", "period"], name="block_unit_period_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~models.Q(period__isempty=True),
                name="block_period_not_empty",
            ),
            ExclusionConstraint(
                name="block_no_overlap_same_unit",
                expressions=[
                    ("unit_id", RangeOperators.EQUAL),
                    ("period", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(unit__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        """نمای خوانا — «چرا این بازه بسته است؟»."""
        scope = self.unit.code if self.unit else "سراسری"
        return f"بلوک {scope} ({self.get_kind_display()})"

    @property
    def start(self) -> datetime | None:
        """آغاز بازهٔ بلوک (برای گزارش)."""
        return self.period.lower if self.period else None

    @property
    def end(self) -> datetime | None:
        """پایان بازهٔ بلوک (برای گزارش)."""
        return self.period.upper if self.period else None
