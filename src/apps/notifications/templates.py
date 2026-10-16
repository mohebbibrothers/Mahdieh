"""قالب‌های پیامک — متن فارسی، کوتاه، بدون لینک کوتاه‌ساز ناشناس.

قواعد نگارش پیام مهدیه:
    • لحن محترمانه و آرام؛ بدون «!»‌های پرهیجان.
    • هر پیام باید در یک نگاه قابل‌فهم باشد (زائر روی موبایل می‌خواند).
    • هیچ دادهٔ حساسی جز آنچه لازم است (کد، تاریخ، واحد) در متن نیست.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.notifications.choices import SmsTemplateKey

TEMPLATES: dict[str, str] = {
    SmsTemplateKey.OTP_LOGIN: (
        "مهدیه\nکد ورود شما: {code}\n"
        "این کد {ttl_minutes} دقیقه اعتبار دارد. آن را در اختیار دیگران نگذارید."
    ),
    SmsTemplateKey.WELCOME: (
        "{full_name} عزیز، پروفایل شما در سامانهٔ زائرسرای مهدیه کامل شد. "
        "از این پس می‌توانید نیم‌روزهای اقامت را رزرو کنید."
    ),
    SmsTemplateKey.BOOKING_CONFIRMED: (
        "مهدیه\nرزرو شما تأیید شد.\nکد رزرو: {code}\nواحد: {unit}\n"
        "ورود: {check_in}\nخروج: {check_out}\nتعداد نفرات: {guests}"
    ),
    SmsTemplateKey.BOOKING_CANCELLED: (
        "مهدیه\nرزرو {code} لغو شد و سانس آزاد گردید. اگر لازم بود می‌توانید بازهٔ دیگری رزرو کنید."
    ),
    SmsTemplateKey.BOOKING_REMINDER: (
        "مهدیه\nیادآوری: پذیرش {unit} برای {check_in} رزرو شده است. منتظر شما هستیم."
    ),
    SmsTemplateKey.OFFER_CREATED: (
        "مهدیه\nسانس مورد نظر شما آزاد شد. برای پذیرش تا {expires_at} فرصت دارید."
    ),
    SmsTemplateKey.STAFF_ALERT: "مهدیه — هشدار عملیاتی: {message}",
    SmsTemplateKey.DRY_RUN: "مهدیه — پیام آزمایشی: {message}",
}


def render_body(template_key: str, context: Mapping[str, Any]) -> str:
    """ساخت متن نهایی پیام از قالب و متغیرها.

    اگر کلید قالب ناشناخته باشد یا متغیری جا بیفتد، متن امن جایگزین برگردانده
    می‌شود؛ پیامک هرگز نباید جریان اصلی را بشکند.
    """
    template = TEMPLATES.get(template_key)
    if template is None:
        return "مهدیه — پیام سیستمی."
    try:
        return template.format(**context)
    except (KeyError, IndexError):
        return "مهدیه — پیام سیستمی."
