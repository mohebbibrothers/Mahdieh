"""زمینهٔ مشترک همهٔ صفحه‌های وب — قواعد زندهٔ اقامت و راه تماس با پذیرش.

چرا این ماژول لازم شد؟ پاورقی سایت قاعده‌های اقامت را **سخت‌کد** کرده بود
(«حداکثر ۱۴ نیم‌روز»، «رزرو تا ۹۰ روز آینده»، «لغو رایگان تا ۲۴ ساعت»). مدیر
می‌تواند همین امروز سهمیه یا افق رزرو را از پنل سیاست عوض کند؛ آن‌وقت سایت
چیزی می‌گوید و موتور چیز دیگری اجرا می‌کند — بدترین نوع ناهماهنگی در یک سامانهٔ
رزرو، چون کاربر فکر می‌کند «سامانه دروغ می‌گوید».

پس قاعده‌ها **یک‌جا** و از سیاست زنده خوانده می‌شوند و در پاورقی، راهنما و برگ
چاپی همان مقدار را نشان می‌دهند. خواندن سیاست کش‌شده است (``load_policy_map``
با کش نسخه‌دار)، پس این کار بار دیتابیس اضافه نمی‌کند.
"""

from __future__ import annotations

from typing import Any, Final

from django.db import Error as DatabaseError
from django.http import HttpRequest

from apps.web import help_content

# تعداد قاعده‌هایی که در پاورقی نشان داده می‌شود (بیش از این، شلوغی است).
FOOTER_RULE_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("min_stay", "کوتاه‌ترین اقامت"),
    ("max_stay", "بلندترین اقامت"),
    ("horizon_days", "افق رزرو (روز)"),
    ("cancel_hours", "لغو رایگان تا (ساعت پیش از ورود)"),
)


def policy(request: HttpRequest) -> dict[str, Any]:  # noqa: ARG001 - قرارداد جنگو
    """افزودن قاعده‌های زندهٔ اقامت و راه تماس به زمینهٔ همهٔ قالب‌ها.

    این تابع در **هر** رندر اجرا می‌شود — از جمله صفحهٔ ۵۰۳ وقتی پایگاه‌داده در
    دسترس نیست. پس هرگز نباید استثنا پرت کند: اگر خواندن سیاست شکست خورد،
    مقادیر پیش‌فرض موتور می‌آید و صفحه‌ها سالم رندر می‌شوند. (این نقص در
    ``tests/test_resilience.py`` گرفته شد: خطای اصلی به ۵۰۰ بدل می‌شد چون
    رندر صفحهٔ خطا هم به دیتابیس وابسته شده بود.)
    """
    try:
        values = help_content.policy_values()
    except DatabaseError:
        values = help_content.default_values()
    try:
        contact = help_content.contact()
    except DatabaseError:  # pragma: no cover - تماس، کوئری نمی‌زند؛ تور نجات
        contact = {"phone": "", "phone_display": "", "address": ""}
    return {
        "STAY": values,
        "STAY_RULES": [
            {"label": label, "value": values.get(key, "—")} for key, label in FOOTER_RULE_KEYS
        ],
        "CONTACT": contact,
    }
