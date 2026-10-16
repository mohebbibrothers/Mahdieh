"""سریالایزرهای مستندسازی داشبورد تحلیلی — قرارداد خواندنی برای فرانت.

این سریالایزرها **سند** هستند، نه اعتبارسنج: خروجی داشبورد را DRF سریال نمی‌کند
(پاسخ یک گزارش آماده است)، ولی فرانت و ابزارهای بیرونی باید بدانند هر کلید چه
شکلی دارد تا کلاینت تایپ‌دار بسازند. پس این‌جا شکل واقعی پاسخ توصیف می‌شود — همان
که ``insights.dashboard()`` و ``InsightsView`` می‌سازند.
"""

from __future__ import annotations

from rest_framework import serializers


class OccupancyPointSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """یک روز از سری اشغال."""

    day = serializers.DateField(help_text="روز میلادی (ISO)")
    state = serializers.CharField(help_text="وضعیت روز: free/tight/busy/…")
    date_jalali = serializers.CharField(help_text="همان روز، جلالی و خوانا")
    weekday = serializers.CharField(help_text="نام روز هفته")
    occupied_cells = serializers.IntegerField(help_text="سانی‌های اشغال‌شده")
    free_cells = serializers.IntegerField(help_text="سانی‌های آزاد")
    total_cells = serializers.IntegerField(help_text="کل سانی‌های روز (واحد × سانی)")
    rate = serializers.FloatField(help_text="نرخ اشغال این روز (۰ تا ۱)")


class RankedDaySerializer(serializers.Serializer):  # type: ignore[type-arg]
    """روز شاخص (شلوغ‌ترین/خلوت‌ترین)."""

    day = serializers.DateField()
    date_jalali = serializers.CharField()
    rate = serializers.FloatField()


class TightDaySerializer(serializers.Serializer):  # type: ignore[type-arg]
    """روز پُر (نزدیک اشباع) در گزارش فشار پیش‌رو."""

    day = serializers.DateField()
    rate = serializers.FloatField()


class PressureSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """فشار پیش‌رو — چند روز خالی است و چند روز پُر؛ مبنای تصمیم پذیرش."""

    start_day = serializers.DateField()
    days = serializers.IntegerField()
    empty_count = serializers.IntegerField()
    tight_count = serializers.IntegerField()
    average_lead_time_days = serializers.FloatField(allow_null=True)
    empty_days = serializers.ListField(child=serializers.DateField())
    tight_days = TightDaySerializer(many=True)


class PlanningRowSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """یک ردیف نقشهٔ حرارتی: نرخ روزبه‌روز یک واحد."""

    code = serializers.CharField(help_text="شناسهٔ واحد")
    title = serializers.CharField()
    floor = serializers.IntegerField()
    rates = serializers.ListField(child=serializers.FloatField())
    average = serializers.FloatField()


class PlanningSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """نقشهٔ حرارتی واحد×روز."""

    start_day = serializers.DateField()
    days = serializers.IntegerField()
    labels = serializers.ListField(child=serializers.CharField())
    rows = PlanningRowSerializer(many=True)


class ForecastRowSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """یک روز از پیش‌بینی تقاضا — میانگین متحرک روزِ هفته، با شفافیت نمونه."""

    day = serializers.DateField()
    date_jalali = serializers.CharField()
    weekday = serializers.CharField()
    expected_occupancy = serializers.FloatField()
    confidence = serializers.CharField(help_text="بالا/پایین — بر پایهٔ تعداد نمونه")
    samples = serializers.IntegerField()


class InsightsSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """کل پاسخ ``GET /api/v1/staff/insights``."""

    start = serializers.DateField(help_text="آغاز پنجرهٔ تحلیل")
    end = serializers.DateField(help_text="پایان پنجرهٔ تحلیل")
    points = OccupancyPointSerializer(many=True)
    occupancy = serializers.FloatField(help_text="نرخ اشغال کل پنجره (۰ تا ۱)")
    trend = serializers.FloatField(help_text="اختلاف نیمهٔ دوم و اول (مثبت ⇒ روبه‌رشد)")
    first_half = serializers.FloatField()
    second_half = serializers.FloatField()
    busiest = RankedDaySerializer(allow_null=True)
    quietest = RankedDaySerializer(allow_null=True)
    fully_free_days = serializers.ListField(child=serializers.DateField())
    days = serializers.IntegerField()
    total_cells = serializers.IntegerField()
    occupied_cells = serializers.IntegerField()
    free_cells = serializers.IntegerField()
    pressure = PressureSerializer()
    planning = PlanningSerializer()
    forecast = ForecastRowSerializer(many=True)
