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
    SmsTemplateKey.BOOKING_MOVED: (
        "مهدیه\n{full_name} عزیز، بازهٔ اقامت شما تغییر کرد.\n"
        "کد رزرو: {code}\nواحد: {unit}\n"
        "ورود تازه: {check_in}\nخروج تازه: {check_out}"
    ),
    SmsTemplateKey.BOOKING_REMINDER: (
        "مهدیه\nیادآوری: پذیرش {unit} برای {check_in} رزرو شده است. منتظر شما هستیم."
    ),
    SmsTemplateKey.OFFER_CREATED: (
        "مهدیه\nسانس مورد نظر شما آزاد شد: {unit}\n"
        "{window}\nمهلت پاسخ: {expires_at}\n\n"
        "پذیرش (یک ضربه):\n{accept_url}\n\n"
        "اگر نمی‌خواهید:\n{decline_url}"
    ),
    SmsTemplateKey.OFFER_ACCEPTED: (
        "مهدیه\nپیشنهاد را پذیرفتید. رزرو شما ثبت شد.\nکد رزرو: {code}\nواحد: {unit}"
    ),
    SmsTemplateKey.WAITLIST_JOINED: (
        "{unit} در بازهٔ {window} درخواست انتظار شما ثبت شد. "
        "نوبت شما: {position}. اگر سانسی آزاد شود، اولین نفر باخبر می‌شوید."
    ),
    SmsTemplateKey.WAITLIST_REQUEUED: (
        "مهدیه\nبرای درخواست {window} پس از {offers} پیشنهاد، سانس مناسبی آزاد نشد؛ "
        "درخواست شما بسته شد. هر وقت خواستید می‌توانید رزرو تازه‌ای ثبت کنید."
    ),
    SmsTemplateKey.FEEDBACK_REQUEST: (
        "{full_name} عزیز، امیدواریم اقامت شما در {unit} خوش گذشته باشد.\n"
        "نظر شما مسیر بهترشدن زائرسرا را می‌سازد؛ پاسخ چند ثانیه‌ای:\n{url}"
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
