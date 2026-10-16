"""سرویس ارسال پیامک — نقطهٔ ورود یکتا برای همهٔ پیام‌های سامانه.

سه تضمین:
    ۱. **هرگز جریان اصلی را نمی‌شکند**: خطای ارائه‌دهنده ⇒ رکورد ناموفق، نه استثنا.
       رزرو نباید به‌خاطر خرابی پنل پیامک شکست بخورد.
    ۲. **حفظ حریم خصوصی**: متن خام ذخیره نمی‌شود؛ نسخهٔ پوشانده می‌ماند.
    ۳. **ضد تکرار**: ``dedup_key`` یکتاست؛ بازپخش Outbox پیام دوباره نمی‌فرستد.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import logging
import re
from typing import Any, cast

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core import metrics
from apps.identity.validators import mask_phone
from apps.notifications.choices import SmsStatus
from apps.notifications.models import SmsDelivery
from apps.notifications.ports import SmsMessage, SmsReceipt
from apps.notifications.providers import get_sms_provider, is_local_provider
from apps.notifications.templates import render_body

logger = logging.getLogger(__name__)

SENSITIVE_DIGITS = re.compile(r"\d{4,}")
BUDGET_KEY = "sms:budget:{day}"
BUDGET_TTL_SECONDS = 60 * 60 * 30


def redact(text: str) -> str:
    """پوشاندن رشته‌های رقمی بلند (کد یک‌بارمصرف، شماره‌ها) در متنِ ذخیره‌شده."""
    return SENSITIVE_DIGITS.sub("••••", text or "")


def _budget_key(day: date) -> str:
    """کلید شمارندهٔ سهمیهٔ روزانهٔ پیامک."""
    return BUDGET_KEY.format(day=day.isoformat())


def daily_usage(day: date | None = None) -> int:
    """تعداد پیامک‌های ارسال‌شده در یک روز (برای نمایش به مدیر)."""
    return int(cache.get(_budget_key(day or timezone.localdate())) or 0)


def _consume_budget(day: date) -> bool:
    """مصرف یک واحد از سهمیهٔ روزانه؛ ``False`` یعنی سهمیه تمام شده است."""
    budget = int(getattr(settings, "SMS_DAILY_BUDGET", 0) or 0)
    if budget <= 0:
        return True
    key = _budget_key(day)
    used = daily_usage(day)
    if used >= budget:
        return False
    try:
        cache.set(key, used + 1, BUDGET_TTL_SECONDS)
    except Exception:  # pragma: no cover - کش هرگز نباید مسیر پیامک را بشکند
        logger.warning("sms_budget_cache_unavailable")
    return True


def can_render(provider_name: str, template_key: str) -> bool:
    """آیا ارائه‌دهندهٔ جاری می‌تواند این قالب را برساند؟

    آداپتورهای متنی: همیشه بله. آداپتورهای الگومحور (ایران‌پیامک): تنها اگر
    الگوی تأییدشده‌ای برای آن قالب وجود داشته باشد. پرسیدن این پرسش **پیش از**
    ساخت رکورد تحویل، دو چیز را تضمین می‌کند: هیچ درخواست بی‌فایده‌ای به پنل
    نمی‌رود، و سرنوشت پیام «مسدود» ثبت می‌شود (نه «ناموفق») تا پایش، تفاوت
    «پنل خوابیده» و «الگو ثبت نشده» را ببیند.
    """
    provider = get_sms_provider(provider_name)
    checker = getattr(provider, "supports_template", None)
    if not callable(checker):  # pragma: no cover - آداپتور بی‌قرارداد
        return True
    return bool(checker(template_key))


def _attempt(provider_name: str, message: SmsMessage) -> SmsReceipt:
    """یک تلاش ارسال با آداپتور مشخص — استثنای ناگهانی هم به رسید ناموفق تبدیل می‌شود."""
    provider = get_sms_provider(provider_name)
    try:
        # آداپتور از رجیستری می‌آید (نوع ``type[Any]``)؛ قرارداد با cast تثبیت می‌شود.
        return cast("SmsReceipt", provider.send(message))
    except Exception as exc:  # pragma: no cover - آداپتورها نباید پرتاب کنند، اما ایمن می‌مانیم
        logger.exception("sms_provider_crashed")
        return SmsReceipt(ok=False, provider=provider_name, error=str(exc))


def send_sms(
    *,
    phone: str,
    template_key: str,
    context: Mapping[str, Any] | None = None,
    dedup_key: str | None = None,
) -> SmsDelivery:
    """ارسال یک پیامک بر پایهٔ قالب، با ثبت کامل سرنوشت آن.

    خروجی همیشه یک ``SmsDelivery`` است؛ فراخوانی‌کننده هرگز لازم نیست
    خطای پیامک را مدیریت کند.
    """
    payload: dict[str, Any] = dict(context or {})
    body = render_body(template_key, payload)
    if dedup_key:
        existing = SmsDelivery.objects.filter(dedup_key=dedup_key).first()
        if existing is not None:
            return existing

    delivery = SmsDelivery.objects.create(
        template_key=template_key,
        phone_masked=mask_phone(phone),
        phone_blind="",
        body_redacted=redact(body),
        context={key: redact(str(value)) for key, value in payload.items()},
        status=SmsStatus.QUEUED,
        dedup_key=dedup_key,
    )

    provider_name = str(getattr(settings, "SMS_PROVIDER", "mock") or "mock")
    enabled = bool(getattr(settings, "SMS_ENABLED", False))
    if not enabled and not is_local_provider(provider_name):
        delivery.mark_blocked(reason=str(_("پیامک در این محیط خاموش است.")))
        metrics.SMS_SENT.labels(provider=provider_name, result="blocked").inc()
        return delivery

    if not _consume_budget(timezone.localdate()):
        delivery.mark_blocked(reason=str(_("سهمیهٔ روزانهٔ پیامک تمام شده است.")))
        metrics.SMS_SENT.labels(provider=provider_name, result="budget").inc()
        logger.warning("sms_daily_budget_exhausted")
        return delivery

    if not can_render(provider_name, template_key):
        delivery.mark_blocked(
            reason=str(
                _(
                    "این قالب هنوز الگویی در پنل پیامک ندارد؛ "
                    "python manage.py sms_patterns --request"
                )
            )
        )
        metrics.SMS_SENT.labels(provider=provider_name, result="pattern_missing").inc()
        logger.warning(
            "sms_template_without_pattern",
            extra={"provider": provider_name, "template_key": template_key},
        )
        return delivery

    message = SmsMessage(phone=phone, body=body, template_key=template_key, context=payload)
    receipt = _attempt(provider_name, message)
    attempts = 1
    fallback_name = str(getattr(settings, "SMS_FALLBACK_PROVIDER", "") or "").strip()
    if not receipt.ok and fallback_name and fallback_name != provider_name:
        # زنجیرهٔ پشتیبان: اختلال یک درگاه نباید پیام «پیشنهاد سانس» یا «کد ورود»
        # را بی‌پاسخ بگذارد؛ آداپتور پشتیبان فقط یک بار و بی‌صدا امتحان می‌شود.
        logger.warning(
            "sms_fallback_attempt", extra={"provider": provider_name, "fallback": fallback_name}
        )
        receipt = _attempt(fallback_name, message)
        attempts = 2

    if attempts > 1:
        delivery.attempts = attempts
        delivery.save(update_fields=["attempts"])

    if receipt.ok:
        delivery.mark_sent(
            provider=receipt.provider, provider_ref=receipt.provider_ref, dry_run=receipt.is_dry_run
        )
        metrics.SMS_SENT.labels(
            provider=receipt.provider, result="dry_run" if receipt.is_dry_run else "sent"
        ).inc()
    else:
        delivery.mark_failed(error=receipt.error or "ارسال ناموفق", provider=receipt.provider)
        metrics.SMS_SENT.labels(provider=receipt.provider, result="failed").inc()
        logger.warning("sms_send_failed", extra={"provider": receipt.provider})
    return delivery
