"""نگهبان پایگاه‌داده: بلوک‌های یک واحد با هم هم‌پوشانی ندارند.

همان الگوی ``reservation/0002_overlap_guards`` (ADR-0020): قفل ردیف واحد، سپس
بررسی هم‌پوشانی نیم‌باز. معنای قید پیشین حفظ شده است:
    • بلوک سراسری (``unit_id IS NULL``) مشمول این قاعده نیست؛
    • ``is_active`` فیلتر نمی‌شود — بلوکِ غیرفعال هم بازه‌اش را نگه می‌دارد تا
      فعال‌سازی دوباره‌اش هرگز به تداخل پنهان نرسد.
خطا: ``SQLSTATE 23000`` / ``MYSQL_ERRNO 4025`` ⇒ ``IntegrityError`` ⇒ ``block_overlap``.
"""

from __future__ import annotations

from django.db import migrations

ERROR_MESSAGE = "block_no_overlap_same_unit"


def _trigger(name: str, event: str, *, exclude_self: bool) -> str:
    """متن تریگر درج/ویرایش بلوک."""
    self_clause = "AND b.id <> NEW.id" if exclude_self else ""
    return f"""
CREATE TRIGGER {name} BEFORE {event} ON inventory_block
FOR EACH ROW
BEGIN
    DECLARE unit_lock INT DEFAULT 0;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET unit_lock = 0;
    IF NEW.unit_id IS NOT NULL THEN
        SELECT 1 INTO unit_lock FROM inventory_unit WHERE id = NEW.unit_id FOR UPDATE;
        IF EXISTS (
            SELECT 1 FROM inventory_block AS b
            WHERE b.unit_id = NEW.unit_id
              AND b.starts_at < NEW.ends_at
              AND NEW.starts_at < b.ends_at
              {self_clause}
        ) THEN
            SIGNAL SQLSTATE '23000'
                SET MESSAGE_TEXT = '{ERROR_MESSAGE}', MYSQL_ERRNO = 4025;
        END IF;
    END IF;
END
"""


class Migration(migrations.Migration):
    """تریگرهای عدم‌هم‌پوشانی بلوک‌های یک واحد (جایگزین قید EXCLUDE)."""

    dependencies = [("inventory", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql=[
                _trigger("block_no_overlap_bi", "INSERT", exclude_self=False),
                _trigger("block_no_overlap_bu", "UPDATE", exclude_self=True),
            ],
            reverse_sql=[
                "DROP TRIGGER IF EXISTS block_no_overlap_bu",
                "DROP TRIGGER IF EXISTS block_no_overlap_bi",
            ],
        ),
    ]
