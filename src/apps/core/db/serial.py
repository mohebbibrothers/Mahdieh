"""قفل تراکنشیِ نام‌دار — یک نویسنده در هر لحظه برای هر نام.

``serialize_on(name)`` تا پایان تراکنش جاری قفل انحصاری ردیف ``name`` در جدول
``core_serial_lock`` را نگه می‌دارد. ردیف در صورت نبودن در **همان دستور** ساخته
می‌شود؛ پس نه مسابقهٔ «ساخت ردیف» هست و نه ارتقای قفل اشتراکی به انحصاری (منشأ
بن‌بست ۱۲۱۳ در الگوی ``get_or_create`` + ``select_for_update``).

ترتیب قفل‌ها: این قفل همیشه **آخر** گرفته می‌شود (پس از قفل کاربر/واحد) و
پس از آن قفل دیگری گرفته نمی‌شود؛ پس چرخهٔ انتظار شکل نمی‌گیرد.
"""

from __future__ import annotations

from django.db import DEFAULT_DB_ALIAS, connections, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.transaction import TransactionManagementError

from apps.core.models import SerialLock

MAX_NAME_LENGTH = 64


def quoted_column(connection: BaseDatabaseWrapper, model: type[models.Model], name: str) -> str:
    """نام نقل‌شدهٔ ستونِ یک فیلد واقعی (برای SQL خام با شناسه‌های امن)."""
    field = model._meta.get_field(name)
    if not isinstance(field, models.Field) or not field.column:
        msg = f"«{name}» ستون پایگاه‌داده ندارد."
        raise TypeError(msg)
    return str(connection.ops.quote_name(field.column))


def serialize_on(name: str, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """قفل انحصاری ``name`` تا پایان تراکنش جاری (بیرون از تراکنش ⇒ خطا)."""
    if not name or len(name) > MAX_NAME_LENGTH:
        msg = f"نام قفل باید ۱ تا {MAX_NAME_LENGTH} نویسه باشد."
        raise ValueError(msg)
    connection = connections[using]
    if not connection.in_atomic_block:
        msg = "serialize_on فقط درون transaction.atomic معنا دارد (قفل با commit آزاد می‌شود)."
        raise TransactionManagementError(msg)
    table = connection.ops.quote_name(SerialLock._meta.db_table)
    column = quoted_column(connection, SerialLock, "name")
    # فقط نام‌های نقل‌شدهٔ جدول/ستون در SQL درج می‌شوند؛ مقدار پارامتر است.
    sql = f"INSERT INTO {table} ({column}) VALUES (%s) ON DUPLICATE KEY UPDATE {column} = {column}"  # noqa: S608  # nosec B608
    with connection.cursor() as cursor:
        cursor.execute(sql, [name])
