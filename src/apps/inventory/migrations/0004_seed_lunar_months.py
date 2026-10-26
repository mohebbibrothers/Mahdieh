"""بذر آغاز رسمی ماه‌های قمری (تقویم رسمی ۱۴۰۵) و برداشتن دو مناسبت نمونهٔ قدیمی seed_inventory.

مقادیر عمداً لیترال‌اند (نه واردات از ``official_calendar``): مهاجرت باید همیشه همان کاری را بکند که
روز نوشتنش می‌کرد. «ایام پایانی ماه صفر» نمونه به تاریخ نادرست ۲۰۲۷-۰۱-۲۰ ثبت شده بود (آخر صفر
۱۴۴۸ در مرداد ۱۴۰۵ است)؛ نسخهٔ رسمی و دقیق آن را همگام‌سازی تقویم رسمی می‌سازد.
"""

from __future__ import annotations

from datetime import date

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

NOTE = "تقویم رسمی ۱۴۰۵ — مؤسسهٔ ژئوفیزیک دانشگاه تهران"
STARTS = (
    (1447, 9, date(2026, 2, 19)),
    (1447, 10, date(2026, 3, 21)),
    (1447, 11, date(2026, 4, 19)),
    (1447, 12, date(2026, 5, 18)),
    (1448, 1, date(2026, 6, 16)),
    (1448, 2, date(2026, 7, 16)),
    (1448, 3, date(2026, 8, 14)),
    (1448, 4, date(2026, 9, 13)),
    (1448, 5, date(2026, 10, 12)),
    (1448, 6, date(2026, 11, 11)),
    (1448, 7, date(2026, 12, 11)),
    (1448, 8, date(2027, 1, 10)),
    (1448, 9, date(2027, 2, 8)),
    (1448, 10, date(2027, 3, 10)),
)
LEGACY_SAMPLES = (("نوروز", date(2027, 3, 21)), ("ایام پایانی ماه صفر", date(2027, 1, 20)))


def forwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """بذر idempotent جدول ماه‌ها + حذف نمونه‌های دستی قدیمی (فقط همان دو ردیف دقیق)."""
    del schema_editor
    lunar = apps.get_model("inventory", "LunarMonthStart")
    for year, month, starts_on in STARTS:
        lunar.objects.update_or_create(
            hijri_year=year, hijri_month=month, defaults={"starts_on": starts_on, "note": NOTE}
        )
    occasion = apps.get_model("inventory", "Occasion")
    for title, day in LEGACY_SAMPLES:
        occasion.objects.filter(title=title, date_gregorian=day, source="manual").delete()


def backwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """برگشت: فقط ردیف‌های بذرشده برداشته می‌شوند."""
    del schema_editor
    lunar = apps.get_model("inventory", "LunarMonthStart")
    for year, month, _starts_on in STARTS:
        lunar.objects.filter(hijri_year=year, hijri_month=month, note=NOTE).delete()


class Migration(migrations.Migration):
    """بذر تقویم قمری رسمی."""

    dependencies = [("inventory", "0003_official_calendar")]

    operations = [migrations.RunPython(forwards, backwards)]
