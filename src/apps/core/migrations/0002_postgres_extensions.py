"""فعال‌سازی پسوندهای موردنیاز PostgreSQL.

چرا در مهاجرت (و نه در اسکریپت راه‌اندازی):
    • تست‌ها هم به این پسوندها نیاز دارند؛ مهاجرت تضمین می‌کند دیتابیس تست
      هم دقیقاً همان قابلیت‌ها را داشته باشد.
    • ``btree_gist`` پیش‌نیاز **قید Exclusion** روی ترکیب کلید برابری و بازه
      است — همان تضمینی که «دوباره‌رزروی» را ناممکن می‌کند (ADR-0002).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

if TYPE_CHECKING:
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.state import StateApps


def create_btree_gist(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """فعال‌سازی ``btree_gist`` — پیش‌نیاز قید عدم‌همپوشانی رزروها."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")


def drop_btree_gist(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """حذف پسوند در بازگردانی مهاجرت (فقط اگر چیز دیگری از آن استفاده نکند)."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP EXTENSION IF EXISTS btree_gist")


class Migration(migrations.Migration):
    """مهاجرت پسوندهای Postgres — پیش‌نیاز تضمین‌های همزمانی."""

    dependencies = [("core", "0001_initial")]
    operations = [
        migrations.RunPython(create_btree_gist, drop_btree_gist),
        TrigramExtension(),
    ]
