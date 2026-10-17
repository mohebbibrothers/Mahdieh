"""خطاهای دامنه — زبان مشترک میان لایهٔ کسب‌وکار و لایهٔ HTTP.

هر خطای دامنه سه چیز دارد:
    ۱. ``code`` — شناسهٔ ماشین‌خوان پایدار (قرارداد برای فرانت).
    ۲. ``status`` — کد وضعیت HTTP متناظر.
    ۳. ``detail`` — پیام انسانی فارسی برای کاربر.
و اختیاراً ``extensions`` — دادهٔ کمکی (مثل فهرست بازه‌های جایگزین).
"""

from __future__ import annotations

from typing import Any, Final

#: وضعیت «ورودی پردازش‌نشدنی» — یگانه وضعیت خطای اعتبارسنجی در قرارداد API.
UNPROCESSABLE_STATUS: Final[int] = 422


class DomainError(Exception):
    """ریشهٔ همهٔ خطاهای دامنه — هرگز مستقیم بالا نمی‌آید."""

    code: str = "domain_error"
    status: int = 400
    title: str = "خطای دامنه"
    default_detail: str = "درخواست قابل پردازش نیست."

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        extensions: dict[str, Any] | None = None,
        errors: dict[str, list[str]] | None = None,
    ) -> None:
        """ساخت خطا با پیام، افزونه‌ها و نگاشت اختیاری خطای فیلدها.

        ``errors`` همان قرارداد ``{"field": ["پیام", ...]}`` پاسخ‌های ۴۲۲ است؛
        فرانت با آن، پیام را دقیقاً زیر همان فیلد فرم نشان می‌دهد (ADR-0017).
        """
        self.detail = detail or self.default_detail
        if code is not None:
            self.code = code
        self.extensions: dict[str, Any] = extensions or {}
        self.errors: dict[str, list[str]] = errors or {}
        super().__init__(self.detail)

    def as_problem(
        self, *, instance: str | None = None, trace_id: str | None = None
    ) -> dict[str, Any]:
        """تبدیل به بدنهٔ استاندارد RFC 9457."""
        problem: dict[str, Any] = {
            "type": f"https://docs.mahdieh.ir/problems/{self.code}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
        }
        if instance is not None:
            problem["instance"] = instance
        if trace_id is not None:
            problem["trace_id"] = trace_id
        problem.update(self.extensions)
        # قرارداد ADR-0017: هر پاسخ ۴۲۲ «همیشه» نگاشت ``errors`` دارد (حتی تهی)؛
        # فرانت هیچ‌گاه لازم نیست وجود کلید را حدس بزند.
        if self.errors or self.status == UNPROCESSABLE_STATUS:
            problem["errors"] = dict(self.errors)
        return problem


class ValidationError(DomainError):
    """ورودی نامعتبر است."""

    code = "validation_error"
    status = 422
    title = "ورودی نامعتبر"
    default_detail = "اطلاعات ارسالی کامل یا درست نیست."


class NotFoundError(DomainError):
    """منبع یافت نشد."""

    code = "not_found"
    status = 404
    title = "یافت نشد"
    default_detail = "منبع درخواستی وجود ندارد یا حذف شده است."


class PermissionDeniedError(DomainError):
    """دسترسی مجاز نیست."""

    code = "permission_denied"
    status = 403
    title = "دسترسی مجاز نیست"
    default_detail = "اجازهٔ انجام این عملیات را ندارید."


class AuthenticationRequiredError(DomainError):
    """ورود لازم است."""

    code = "authentication_required"
    status = 401
    title = "ورود لازم است"
    default_detail = "برای این عملیات ابتدا وارد شوید."


class ConflictError(DomainError):
    """تصادم با وضعیت موجود (مثلاً رزرو هم‌زمان)."""

    code = "conflict"
    status = 409
    title = "تصادم"
    default_detail = "این عملیات با وضعیت فعلی سیستم سازگار نیست."


class StaleResourceError(ConflictError):
    """رکورد پس از خواندن شما تغییر کرده — قفل خوش‌بینانه."""

    code = "stale_resource"
    title = "اطلاعات قدیمی"
    default_detail = "این مورد توسط کاربر دیگری تغییر کرده است؛ صفحه را بازخوانی کنید."


class ReservationConflictError(ConflictError):
    """بازهٔ درخواستی در همان لحظه اشغال شد.

    این خطا ستون فقرات تجربهٔ کاربری در تصادم‌ها است: به‌جای پیام خام
    دیتابیس، فهرست بازه‌های جایگزینِ آزاد را هم برمی‌گرداند.
    """

    code = "reservation_conflict"
    title = "این بازه اشغال شد"
    default_detail = "متأسفانه در همین لحظه بازهٔ انتخابی رزرو شد."


class PolicyViolationError(DomainError):
    """نقض قاعدهٔ سیاست (مدت، سهمیه، خنک‌شدن و …)."""

    code = "policy_violation"
    status = 422
    title = "قاعدهٔ رزرو رعایت نشده"
    default_detail = "درخواست با قواعد جاری رزرو سازگار نیست."


class RateLimitedError(DomainError):
    """محدودیت نرخ/سوءاستفاده."""

    code = "rate_limited"
    status = 429
    title = "تعداد درخواست زیاد"
    default_detail = "کمی بعد دوباره تلاش کنید."


class ProviderUnavailableError(DomainError):
    """سرویس بیرونی در دسترس نیست (پیامک، ذخیره‌سازی)."""

    code = "provider_unavailable"
    status = 503
    title = "سرویس بیرونی در دسترس نیست"
    default_detail = "ارتباط با سرویس جانبی برقرار نشد."


class IdempotencyConflictError(ConflictError):
    """همان کلید هم‌توانی با بدنهٔ متفاوت استفاده شده است."""

    code = "idempotency_key_reused"
    title = "کلید هم‌توانی تکراری"
    default_detail = "این کلید قبلاً با درخواست دیگری استفاده شده است."


class GoneError(DomainError):
    """منبع وجود داشته ولی دیگر قابل استفاده نیست (پیوند منقضی/مصرف‌شده)."""

    code = "gone"
    status = 410
    title = "دیگر در دسترس نیست"
    default_detail = "مهلت این پیوند به پایان رسیده یا قبلاً استفاده شده است."
