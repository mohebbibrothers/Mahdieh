"""کش پایگاه‌داده با ``incr``/``decr`` اتمیک — جانشین Valkey روی میزبان اشتراکی.

چرا کش جنگو روی جدول؟ میزبان cPanel سرویس Redis ندارد (ADR-0021). ``DatabaseCache``
رسمی جنگو همه‌چیز را دارد **جز اتمیک‌بودن شمارنده**: ``incr`` پیش‌فرضش «بخوان،
جمع بزن، بنویس» است و دو فرآیند Passenger هم‌زمان یک شمارش را گم می‌کنند. شمارندهٔ
قفل MFA (``identity.mfa``)، نسخهٔ کش تقویم (``core.cache``) و شمارندهٔ سدِ ظرفیت
همه به جمعِ دقیق نیاز دارند؛ پس ``incr`` اینجا زیر قفل ردیفی
(``SELECT … FOR UPDATE``) انجام می‌شود.

جداسازی تراکنش: کش روی **اتصال جداگانه** (``DATABASES["cache"]``، ر.ک.
``apps.core.db.routers``) و در حالت autocommit اجرا می‌شود؛ پس شمارندهٔ تلاش
ناموفق با برگشت تراکنش درخواست پاک نمی‌شود — همان معنای Redis.

قالب ذخیره دقیقاً همان قالب ``DatabaseCache`` است (pickle + base64) تا ``get``
و ``set`` والد بدون تغییر کار کنند.
"""

from __future__ import annotations

import base64
from datetime import datetime
import pickle  # nosec B403 - همان قالب DatabaseCache جنگو؛ داده فقط به دست خود برنامه نوشته می‌شود
from typing import Any

from django.core.cache.backends.db import DatabaseCache
from django.db import connections, models, router, transaction
from django.utils.timezone import now as tz_now


class AtomicDatabaseCache(DatabaseCache):
    """``DatabaseCache`` با ``incr``/``decr`` اتمیک (قفل ردیفی، بدون مسابقه)."""

    #: نام جدول کش؛ ``BaseDatabaseCache.__init__`` آن را از ``LOCATION`` می‌سازد.
    _table: str

    def incr(self, key: Any, delta: int = 1, version: int | None = None) -> int:
        """افزایش اتمیک؛ کلید ناموجود یا منقضی ⇒ ``ValueError`` (قرارداد کش جنگو)."""
        cache_key = self.make_and_validate_key(key, version=version)
        db = router.db_for_write(self.cache_model_class)
        connection = connections[db]
        quote = connection.ops.quote_name
        table = quote(self._table)
        expression = models.Expression(output_field=models.DateTimeField())
        converters = connection.ops.get_db_converters(expression) + expression.get_db_converters(
            connection
        )

        # در SQL فقط نام‌های جدول/ستونِ نقل‌شده با quote_name درج می‌شوند؛ مقدارها پارامترند.
        with transaction.atomic(using=db), connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {quote('value')}, {quote('expires')} FROM {table} "  # noqa: S608  # nosec B608
                f"WHERE {quote('cache_key')} = %s FOR UPDATE",
                [cache_key],
            )
            row = cursor.fetchone()
            if row is None:
                msg = f"Key '{key}' not found"
                raise ValueError(msg)
            raw_value, expires = row
            for converter in converters:
                expires = converter(expires, expression, connection)
            if isinstance(expires, datetime) and expires < tz_now():
                msg = f"Key '{key}' not found"
                raise ValueError(msg)
            current = pickle.loads(  # noqa: S301  # nosec B301 - دادهٔ نوشته‌شده به دست همین کش
                base64.b64decode(connection.ops.process_clob(raw_value).encode(), validate=True)
            )
            new_value = int(current) + delta
            encoded = base64.b64encode(pickle.dumps(new_value, self.pickle_protocol)).decode(
                "latin1"
            )
            cursor.execute(
                f"UPDATE {table} SET {quote('value')} = %s "  # noqa: S608  # nosec B608
                f"WHERE {quote('cache_key')} = %s",
                [encoded, cache_key],
            )
        return new_value

    def decr(self, key: Any, delta: int = 1, version: int | None = None) -> int:
        """کاهش اتمیک — همان ``incr`` با علامت منفی."""
        return self.incr(key, -delta, version=version)
