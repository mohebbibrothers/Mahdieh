"""لینک‌های امضاشدهٔ صف انتظار — «یک‌ضربه‌ای» بدون ورود دوباره.

مسئله: زائر پیامک پیشنهاد را روی موبایل می‌خواند؛ نمی‌خواهیم برای پذیرش، دوباره
از مسیر ورود با کد یک‌بارمصرف بگذرد (اصطکاک = از دست رفتن سانس).

راه‌حل: توکن امضاشده با کلید سایت که **فقط** به یک پیشنهاد مشخص و یک کنش
محدود است و مهلت دارد. توکن نه حدس‌زدنی است و نه قابل استفادهٔ دوباره پس از
پاسخ. امضا با ``SECRET_KEY`` انجام می‌شود؛ هیچ کلید جداگانه‌ای در محیط لازم نیست.
"""

from __future__ import annotations

from typing import Any, Final

from django.core import signing

SALT: Final[str] = "mahdieh.waitlist.offer"
ACCEPT: Final[str] = "accept"
DECLINE: Final[str] = "decline"
ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset({ACCEPT, DECLINE})


class OfferTokenError(Exception):
    """توکن نامعتبر، دست‌کاری‌شده یا منقضی."""


def make_token(*, offer_id: Any, action: str) -> str:
    """ساخت توکن امضاشدهٔ یک کنش روی یک پیشنهاد.

    مهلت در خودِ توکن نگه‌داری نمی‌شود: امضای جنگو زمان ساخت را درون توکن دارد و
    هنگام خواندن (``read_token``) با ``max_age`` سنجیده می‌شود. داشتن مهلت در دو
    جا، فقط دو منبع حقیقتِ ناسازگار می‌سازد.
    """
    if action not in ALLOWED_ACTIONS:  # pragma: no cover - محافظ برنامه‌نویسی
        msg = f"کنش «{action}» برای توکن پشتیبانی نمی‌شود."
        raise ValueError(msg)
    return signing.dumps({"offer": str(offer_id), "action": action}, salt=SALT, compress=True)


def offer_urls(offer_id: Any, *, base_url: str | None = None) -> dict[str, str]:
    """نشانی‌های مطلق «پذیرش» و «رد» برای درج در پیامک.

    مسیرها همان مسیرهای وب‌اند (``/offer/<token>/<action>/``)؛ چون توکن امضاشده
    و مخصوص یک کنش است، حتی اگر لینک دست‌کاری شود هیچ کنش دیگری اجرا نمی‌شود.
    """
    from apps.core.site import absolute_url

    return {
        action: absolute_url(
            f"/offer/{make_token(offer_id=offer_id, action=action)}/{action}/",
            base_url=base_url,
        )
        for action in sorted(ALLOWED_ACTIONS)
    }


def read_token(token: str, *, expected_action: str, max_age_seconds: int) -> str:
    """اعتبارسنجی توکن و بازگشت شناسهٔ پیشنهاد.

    مهلت توکن همان مهلت پیشنهاد است (پیش‌فرض: پنجرهٔ سیاست). تفاوت اندک زمان
    بین ساخت پیامک و کلیک زائر، با ۶۰ ثانیه ارفاق پوشش داده می‌شود.
    """
    if expected_action not in ALLOWED_ACTIONS:  # pragma: no cover
        msg = f"کنش «{expected_action}» پشتیبانی نمی‌شود."
        raise ValueError(msg)
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age_seconds + 60)
    except signing.SignatureExpired as exc:
        raise OfferTokenError("مهلت این لینک گذشته است.") from exc
    except signing.BadSignature as exc:
        raise OfferTokenError("این لینک معتبر نیست.") from exc
    if str(payload.get("action")) != expected_action:
        raise OfferTokenError("این لینک برای این کنش ساخته نشده است.")
    offer_id = payload.get("offer")
    if not offer_id:  # pragma: no cover - توکن‌های ما همیشه شناسه دارند
        raise OfferTokenError("این لینک معتبر نیست.")
    return str(offer_id)
