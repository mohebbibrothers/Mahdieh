"""تشخیص خطای گذرای زیرساخت — یک قاعده، یک منبع حقیقت.

چرا ماژول جدا؟ چون دو لایه باید **یکسان** قضاوت کنند:
    • ``apps.core.middleware.DatabaseResilienceMiddleware`` ⇒ برای مسیرهای HTML
    • ``apps.core.problems.problem_details_exception_handler`` ⇒ برای API

اگر هر لایه فهرست پیام‌ها و معیار خودش را داشت، دیر یا زود واگرا می‌شدند و
همان باگ برمی‌گشت («چرا صفحهٔ سایت ۵۰۳ است ولی API همان اتفاق را ۵۰۰ می‌دهد؟»).

قاعدهٔ تشخیص عمداً محافظه‌کارانه است: تنها خطاهایی «گذرا» شمرده می‌شوند که
به **اتصال** مربوط‌اند (سرور در دسترس نیست، اتصال‌ها تمام شده، اتصال بسته شده).
خطای کد — مثل نبود جدول یا اشتباه نوشتاری در کوئری — گذرا نیست و باید ۵۰۰ بماند
تا در پایش به‌عنوان باگ دیده شود، نه «ظرفیت».
"""

from __future__ import annotations

from django.db.utils import InterfaceError, OperationalError

#: نوع خطاهایی که «می‌توان دوباره تلاش کرد» را می‌رسانند.
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (OperationalError, InterfaceError)

#: نشانه‌های شکست اتصال در پیام درایور یا سرور (با حروف کوچک مقایسه می‌شوند).
TRANSIENT_HINTS: tuple[str, ...] = (
    "too many clients",
    "connection refused",
    "connection to server",
    "could not connect",
    "server closed the connection",
    "terminating connection",
    "timeout expired",
    "connection pool is full",
    "pool timeout",
    "is the server running",
)


def is_transient_infrastructure_error(exc: BaseException) -> bool:
    """آیا این خطا نشانهٔ قطعیِ گذرای زیرساخت است (⇒ ۵۰۳، نه ۵۰۰)؟"""
    if not isinstance(exc, RETRYABLE_EXCEPTIONS):
        return False
    message = str(exc).lower()
    return any(hint in message for hint in TRANSIENT_HINTS)


def service_unavailable_problem_detail() -> str:
    """پیام یکسانِ کاربرپسند برای هر دو مسیر (HTML و API)."""
    return "ارتباط با پایگاه‌داده برقرار نشد؛ چند لحظه بعد دوباره تلاش کنید."


RETRY_AFTER_SECONDS = 5
SERVICE_UNAVAILABLE_STATUS = 503
