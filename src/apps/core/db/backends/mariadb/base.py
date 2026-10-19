"""درایور MariaDB مهدیه (``ENGINE = "apps.core.db.backends.mariadb"``).

همان ``django.db.backends.mysql`` رسمی است با دو اصلاح سنجیده:

۱. **مقایسهٔ متن مثل PostgreSQL**: collation پیش‌فرض پایگاه‌داده
   ``utf8mb4_uca1400_as_cs`` است (حساس به حروف و اعراب) تا یکتایی و برابری
   دقیق بماند — کلید ایدمپوتنسی ``Ab`` و ``aB`` دو کلید جدا هستند، «آ» و «ا»
   یکی نمی‌شوند. جستجوهای ``i*`` (``iexact``، ``icontains`` …) اما باید حساس
   به حروف نباشند؛ پس فقط برای همین عملگرها collation صریحِ ``as_ci`` می‌آید.
۲. **منطقهٔ زمانی بدون جدول tz** — ``operations.DatabaseOperations``.
"""

from __future__ import annotations

from django.db.backends.mysql import base

from .operations import DatabaseOperations

CI_COLLATION = "utf8mb4_uca1400_as_ci"


class DatabaseWrapper(base.DatabaseWrapper):
    """اتصال MariaDB با عملگرهای حساس‌نبودن به حروف و تبدیل زمانی مستقل."""

    ops_class = DatabaseOperations

    operators = {
        **base.DatabaseWrapper.operators,
        "iexact": f"LIKE %s COLLATE {CI_COLLATION}",
        "icontains": f"LIKE %s COLLATE {CI_COLLATION}",
        "istartswith": f"LIKE %s COLLATE {CI_COLLATION}",
        "iendswith": f"LIKE %s COLLATE {CI_COLLATION}",
    }
    pattern_ops = {
        **base.DatabaseWrapper.pattern_ops,
        "icontains": f"LIKE CONCAT('%%', {{}}, '%%') COLLATE {CI_COLLATION}",
        "istartswith": f"LIKE CONCAT({{}}, '%%') COLLATE {CI_COLLATION}",
        "iendswith": f"LIKE CONCAT('%%', {{}}) COLLATE {CI_COLLATION}",
    }
