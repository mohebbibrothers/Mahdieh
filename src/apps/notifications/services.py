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
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core import metrics
from apps.identity.validators import mask_phone
from apps.notifications.choices import SmsStatus
from apps.notifications.models import SmsDelivery
from apps.notifications.ports import SmsMessage
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

    provider = get_sms_provider(provider_name)
    try:
        receipt = provider.send(
            SmsMessage(phone=phone, body=body, template_key=template_key, context=payload)
        )
    except Exception as exc:  # pragma: no cover - آداپتورها نباید پرتاب کنند، اما ایمن می‌مانیم
        logger.exception("sms_provider_crashed")
        delivery.mark_failed(error=str(exc), provider=provider_name)
        metrics.SMS_SENT.labels(provider=provider_name, result="error").inc()
        return delivery

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
