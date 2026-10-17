"""بافر نظافت واحد: «بازنویسی اختیاری» به‌جای عدد ثابت ۶۰.

تصمیم کارفرما (۱۴۰۵/۰۷): فعلاً بی‌بافر. پیش‌تر هر واحد عدد ثابت ۶۰ داشت و
سیاست سراسری عملاً نادیده گرفته می‌شد (``unit.value or policy``). اکنون:
    • ``NULL``  ⇒ تابع سیاست سراسری ``lattice.default_cleaning_buffer_minutes``؛
    • عدد صریح ⇒ بازنویسی همان واحد (۰ یعنی صریحاً بی‌بافر).
مقادیر پیش‌فرض قدیمی (۶۰) به ``NULL`` برمی‌گردند؛ مقدار دستی دیگر دست نمی‌خورد.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations, models

LEGACY_DEFAULT_MINUTES = 60


def forwards(apps: Any, schema_editor: Any) -> None:
    """واحدهایی که هنوز پیش‌فرض قدیمی را دارند، تابع سیاست می‌شوند."""
    del schema_editor
    unit_model = apps.get_model("inventory", "Unit")
    unit_model.objects.filter(cleaning_buffer_minutes=LEGACY_DEFAULT_MINUTES).update(
        cleaning_buffer_minutes=None
    )


def backwards(apps: Any, schema_editor: Any) -> None:
    """بازگشت: مقدار تهی به پیش‌فرض قدیمی برمی‌گردد تا ستون NOT NULL شود."""
    del schema_editor
    unit_model = apps.get_model("inventory", "Unit")
    unit_model.objects.filter(cleaning_buffer_minutes__isnull=True).update(
        cleaning_buffer_minutes=LEGACY_DEFAULT_MINUTES
    )


class Migration(migrations.Migration):
    """ستون تهی‌پذیر + انتقال داده در یک مهاجرت (ترتیب: ستون ⇒ داده)."""

    dependencies = [("inventory", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="unit",
            name="cleaning_buffer_minutes",
            field=models.PositiveSmallIntegerField(
                blank=True,
                default=None,
                help_text="خالی = تابع سیاست سراسری «بافر نظافت»؛ صفر = صریحاً بدون بافر.",
                null=True,
                verbose_name="بافر نظافت (دقیقه)",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
