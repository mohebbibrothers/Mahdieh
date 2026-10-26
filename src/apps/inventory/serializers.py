"""سریال‌ایزرهای موجودی — قرارداد خواندنی برای تقویم و صفحهٔ واحدها."""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.serializers import PeriodRangeField
from apps.inventory.models import Block, Floor, Occasion, Unit, UnitFeature, UnitPhoto


class UnitFeatureSerializer(serializers.ModelSerializer[UnitFeature]):
    """نمایش یک امکان واحد — با یادداشت ویژهٔ همان واحد (مثلاً «داخل واحد»)."""

    note = serializers.CharField(
        read_only=True, help_text="یادداشت عمومی این امکان برای همین واحد؛ رشتهٔ تهی یعنی ندارد"
    )

    class Meta:
        model = UnitFeature
        fields = ("code", "title", "icon", "is_highlight", "note")


class UnitCoverSerializer(serializers.Serializer[Any]):
    """تصویر شاخص کارت واحد (عکس واقعی بارگذاری‌شده در پنل)."""

    url = serializers.CharField()
    alt_text = serializers.CharField()


class UnitPhotoSerializer(serializers.ModelSerializer[UnitPhoto]):
    """نمایش تصویر واحد."""

    url = serializers.FileField(source="image", read_only=True)

    class Meta:
        model = UnitPhoto
        fields = ("id", "url", "alt_text", "is_cover", "sort_order")


class UnitSerializer(serializers.ModelSerializer[Unit]):
    """نمایش واحد — دقیقاً آنچه تقویم و کارت واحد لازم دارد."""

    floor_number = serializers.IntegerField(read_only=True)
    type_label = serializers.CharField(read_only=True)
    capacity_total = serializers.IntegerField(read_only=True)
    features = serializers.SerializerMethodField()
    cover = serializers.SerializerMethodField()

    class Meta:
        model = Unit
        fields = (
            "id",
            "code",
            "title",
            "unit_type",
            "type_label",
            "floor_number",
            "capacity_adults",
            "capacity_children",
            "capacity_total",
            "cleaning_buffer_minutes",
            "features",
            "cover",
        )

    @extend_schema_field(UnitFeatureSerializer(many=True))
    def get_features(self, obj: Unit) -> list[dict[str, object]]:
        """امکانات واحد — از پیش‌بارگذاری‌شده اگر موجود باشد (بدون N+1)."""
        links = getattr(obj, "feature_links", None)
        if links is None:  # pragma: no cover - مسیر بدون prefetch
            return []
        return [
            {
                "code": link.feature.code,
                "title": link.feature.title,
                "icon": link.feature.icon,
                "is_highlight": link.feature.is_highlight,
                "note": link.note,
            }
            for link in links.all()
        ]

    @extend_schema_field(UnitCoverSerializer(allow_null=True))
    def get_cover(self, obj: Unit) -> dict[str, str] | None:
        """تصویر شاخص (یا نخستین تصویر) از پیش‌بارگذاری؛ بی‌تصویر ⇒ ``null`` و فرانت پلان مفهومی را نشان می‌دهد."""
        photos = list(obj.photos.all())
        if not photos:
            return None
        photo = next((item for item in photos if item.is_cover), photos[0])
        request = self.context.get("request")
        url = photo.image.url
        return {
            "url": request.build_absolute_uri(url) if request is not None else url,
            "alt_text": photo.alt_text or f"تصویر {obj.title}",
        }


class UnitDetailSerializer(UnitSerializer):
    """نمای کامل واحد — با همهٔ تصاویر.

    ``notes`` («یادداشت کارکنان») عمداً اینجا نیست: یادداشت داخلی است و نباید در API عمومی
    منتشر شود (طراحی P-02/P-03 هم صراحتاً آن را نمایش نمی‌دهد).
    """

    photos = UnitPhotoSerializer(many=True, read_only=True)

    class Meta(UnitSerializer.Meta):
        fields: tuple[str, ...] = (  # type: ignore[assignment]
            *UnitSerializer.Meta.fields,
            "photos",
        )


class FloorSerializer(serializers.ModelSerializer[Floor]):
    """نمایش طبقه با واحدهای فعال آن."""

    units = UnitSerializer(many=True, read_only=True)

    class Meta:
        model = Floor
        fields = ("number", "title", "description", "accessibility_notes", "units")


class OccasionSerializer(serializers.ModelSerializer[Occasion]):
    """نمایش مناسبت برای رنگ‌آمیزی تقویم."""

    is_peak = serializers.BooleanField(read_only=True)

    class Meta:
        model = Occasion
        fields = (
            "id",
            "title",
            "kind",
            "date_gregorian",
            "date_jalali",
            "demand_level",
            "is_peak",
            "is_holiday",
            "hijri_date",
            "is_provisional",
            "source",
            "description",
        )


class BlockSerializer(serializers.ModelSerializer[Block]):
    """نمایش بلوک نگهداری — برای پنل کارکنان (دلیل در تقویم عمومی نمی‌آید).

    ``period`` صریح و ساخت‌یافته است (``{"start", "end"}``) نه رشتهٔ JSON فرارکرده؛
    تایپ‌های کلاینت فرانت باید مستقیم از سند OpenAPI ساخته شوند.
    """

    unit_code = serializers.CharField(source="unit.code", read_only=True, default=None)
    period = PeriodRangeField(read_only=True)

    class Meta:
        model = Block
        fields = ("id", "unit_code", "period", "kind", "reason", "created_at")


class BlockCreateSerializer(serializers.Serializer[Any]):
    """ورودی ثبت بلوک نگهداری توسط کارکنان (دلیل اجباری)."""

    unit_code = serializers.CharField(
        max_length=8, required=False, allow_blank=True, help_text="تهی ⇒ بلوک سراسری"
    )
    start = serializers.DateTimeField(help_text="آغاز بلوک (ISO 8601)")
    end = serializers.DateTimeField(help_text="پایان بلوک (ISO 8601)")
    kind = serializers.CharField(max_length=16)
    reason = serializers.CharField(max_length=300)
