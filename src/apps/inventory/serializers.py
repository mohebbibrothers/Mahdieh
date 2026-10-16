"""سریال‌ایزرهای موجودی — قرارداد خواندنی برای تقویم و صفحهٔ واحدها."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.inventory.models import Block, Floor, Occasion, Unit, UnitFeature, UnitPhoto


class UnitFeatureSerializer(serializers.ModelSerializer[UnitFeature]):
    """نمایش یک امکان واحد."""

    class Meta:
        model = UnitFeature
        fields = ("code", "title", "icon", "is_highlight")


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
        )

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
            }
            for link in links.all()
        ]


class UnitDetailSerializer(UnitSerializer):
    """نمای کامل واحد — با تصاویر و یادداشت عمومی."""

    photos = UnitPhotoSerializer(many=True, read_only=True)

    class Meta(UnitSerializer.Meta):
        fields: tuple[str, ...] = (  # type: ignore[assignment]
            *UnitSerializer.Meta.fields,
            "photos",
            "notes",
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
            "description",
        )


class BlockSerializer(serializers.ModelSerializer[Block]):
    """نمایش بلوک نگهداری — برای پنل کارکنان (دلیل در تقویم عمومی نمی‌آید)."""

    unit_code = serializers.CharField(source="unit.code", read_only=True, default=None)

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
