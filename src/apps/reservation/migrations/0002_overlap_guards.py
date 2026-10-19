"""نگهبان پایگاه‌دادهٔ ثابت I1: هیچ دو رزرو فعال روی یک واحد هم‌پوشانی ندارند.

MariaDB قید ``EXCLUDE`` ندارد؛ جایگزین استاندارد و اثبات‌شده دو تریگر ``BEFORE``
است (ADR-0020):

1. ردیف واحد (``inventory_unit``) با ``FOR UPDATE`` قفل می‌شود. قفل روی جدول
   والد است، نه خود جدول رزرو (قفل/نوشتن روی جدول خودِ تریگر خطای 1442 می‌دهد).
   هر درج/ویرایش فعالِ هم‌زمان روی همان واحد پشت این قفل صف می‌کشد؛ در
   ``READ COMMITTED`` نفر دوم پس از گرفتن قفل، ردیف تأییدشدهٔ نفر اول را می‌بیند.
2. اگر رزرو فعالِ دیگری با **بازهٔ مسدودکننده** هم‌پوشانی نیم‌باز داشته باشد
   (``start < other_end AND other_start < end``؛ بازه‌های مجاور تداخل ندارند)،
   ``SIGNAL SQLSTATE '23000'`` با ``MYSQL_ERRNO = 4025`` (ER_CONSTRAINT_FAILED)
   زده می‌شود که جنگو آن را ``IntegrityError`` می‌کند ⇒ سرویس، پیام «تداخل»
   با بازه‌های جایگزین برمی‌گرداند.

فهرست وضعیت‌های فعال عمداً در همین مهاجرت **منجمد** است (مهاجرت تاریخ است، نه
کد زنده). آزمون ``test_overlap_trigger_tracks_active_statuses`` متن تریگر نصب‌شده
را با ``ReservationStatus.active_statuses()`` مقایسه می‌کند؛ اگر روزی وضعیت فعال
تازه‌ای افزوده شد، آزمون شکست می‌خورد تا مهاجرت تازه‌ای برای تریگر نوشته شود.
"""

from __future__ import annotations

from django.db import migrations

ACTIVE_STATUSES_SQL = "'hold', 'confirmed', 'checked_in'"
ERROR_MESSAGE = "no_overlapping_active_reservations"


def _trigger(name: str, event: str, *, exclude_self: bool) -> str:
    """متن تریگر درج/ویرایش (تنها تفاوت: کنار گذاشتن ردیف خودش در ویرایش)."""
    self_clause = "AND r.id <> NEW.id" if exclude_self else ""
    return f"""
CREATE TRIGGER {name} BEFORE {event} ON reservation_reservation
FOR EACH ROW
BEGIN
    DECLARE unit_lock INT DEFAULT 0;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET unit_lock = 0;
    IF NEW.status IN ({ACTIVE_STATUSES_SQL}) THEN
        SELECT 1 INTO unit_lock FROM inventory_unit WHERE id = NEW.unit_id FOR UPDATE;
        IF EXISTS (
            SELECT 1 FROM reservation_reservation AS r
            WHERE r.unit_id = NEW.unit_id
              AND r.status IN ({ACTIVE_STATUSES_SQL})
              AND r.block_starts_at < NEW.block_ends_at
              AND NEW.block_starts_at < r.block_ends_at
              {self_clause}
        ) THEN
            SIGNAL SQLSTATE '23000'
                SET MESSAGE_TEXT = '{ERROR_MESSAGE}', MYSQL_ERRNO = 4025;
        END IF;
    END IF;
END
"""


class Migration(migrations.Migration):
    """تریگرهای عدم‌هم‌پوشانی رزرو فعال (جایگزین قید EXCLUDE)."""

    dependencies = [
        ("reservation", "0001_initial"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                _trigger("reservation_no_overlap_bi", "INSERT", exclude_self=False),
                _trigger("reservation_no_overlap_bu", "UPDATE", exclude_self=True),
            ],
            reverse_sql=[
                "DROP TRIGGER IF EXISTS reservation_no_overlap_bu",
                "DROP TRIGGER IF EXISTS reservation_no_overlap_bi",
            ],
        ),
    ]
