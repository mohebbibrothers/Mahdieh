"""مدل دفتر تحویل پیامک — «چه فرستادیم، چه شد، و با چه کلیدی».

اصل حریم خصوصی: **بدنهٔ پیام ذخیره نمی‌شود**. پیامک‌های مهدیه کد یک‌بارمصرف
و اطلاعات رزرو دارند؛ نگه‌داری متن خام، راز را در دیتابیس و بکاپ پخش می‌کند.
به‌جای آن، نسخهٔ **پوشانده** (``body_redacted``) برای پشتیبانی و عیب‌یابی
ذخیره می‌شود: اعداد بلندِ حساس با «•» جایگزین می‌شوند.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.uuid7 import uuid7
from apps.notifications.choices import SmsStatus


class SmsDelivery(models.Model):
    """یک تلاش ارسال پیامک — رکورد نگه‌داشت‌شده برای پشتیبانی و تحلیل هزینه."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False, verbose_name=_("شناسه"))
    template_key = models.CharField(max_length=64, db_index=True, verbose_name=_("کلید قالب"))
    phone_masked = models.CharField(max_length=24, verbose_name=_("تلفن پوشانده"))
    phone_blind = models.CharField(
        max_length=64, blank=True, default="", db_index=True, verbose_name=_("ایندکس کور تلفن")
    )
    body_redacted = models.TextField(verbose_name=_("متن پوشانده"))
    context = models.JSONField(
        default=dict, blank=True, verbose_name=_("متغیرهای قالب (پاک‌سازی‌شده)")
    )
    status = models.CharField(
        max_length=16,
        choices=SmsStatus.choices,
        default=SmsStatus.QUEUED,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    provider = models.CharField(max_length=40, blank=True, default="", verbose_name=_("ارائه‌دهنده"))
    provider_ref = models.CharField(
        max_length=80, blank=True, default="", verbose_name=_("مرجع ارائه‌دهنده")
    )
    error = models.CharField(max_length=300, blank=True, default="", verbose_name=_("خطا"))
    dedup_key = models.CharField(
        max_length=120,
        null=True,
        blank=True,
        unique=True,
        verbose_name=_("کلید یکتایی"),
        help_text=_("جلوگیری از ارسال دوبارهٔ همان پیام در بازپخش‌ها"),
    )
    attempts = models.PositiveSmallIntegerField(default=1, verbose_name=_("تلاش‌ها"))
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name=_("زمان ایجاد")
    )
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان ارسال"))

    class Meta:
        verbose_name = _("تحویل پیامک")
        verbose_name_plural = _("دفتر تحویل پیامک")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["template_key", "status"], name="sms_template_status_idx"),
            models.Index(fields=["created_at", "status"], name="sms_created_status_idx"),
        ]

    def __str__(self) -> str:
        """نمای خوانا برای پنل مدیریت."""
        return f"{self.template_key} → {self.phone_masked} [{self.status}]"

    def mark_sent(self, *, provider: str, provider_ref: str, dry_run: bool) -> None:
        """ثبت ارسال موفق (یا ارسال آزمایشی در محیط توسعه)."""
        self.status = SmsStatus.SUPPRESSED if dry_run else SmsStatus.SENT
        self.provider = provider
        self.provider_ref = provider_ref
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "provider", "provider_ref", "sent_at"])

    def mark_failed(self, *, error: str, provider: str) -> None:
        """ثبت شکست ارسال — با متن خطای کوتاه و بدون دادهٔ حساس."""
        self.status = SmsStatus.FAILED
        self.error = error[:300]
        self.provider = provider
        self.save(update_fields=["status", "error", "provider"])

    def mark_blocked(self, *, reason: str) -> None:
        """ثبت مسدودی (سهمیهٔ روزانه، خاموشی سامانهٔ پیامک)."""
        self.status = SmsStatus.BLOCKED
        self.error = reason[:300]
        self.save(update_fields=["status", "error"])

    def requeue(self, *, body: str, context_data: dict[str, str], attempts: int = 1) -> None:
        """بازآماده‌سازی یک رکورد **مسدود** برای تلاش تازه.

        چرا این متد لازم است؟ «مسدود» وضعیت *پیکربندی* است، نه وضعیت *رویداد*:
        سامانه خاموش بود، سهمیهٔ روز تمام شده بود، یا الگوی پنل هنوز نرسیده بود.
        کلید یکتایی (``dedup_key``) اما برای همیشه قفل می‌ماند؛ نتیجه: همان روزی
        که الگو از پنل می‌رسد، پیام‌های مسدودشده هرگز فرستاده نمی‌شوند — نقصی
        بی‌صدا. پس مسدود را «زنده» می‌کنیم، ولی ارسال‌شده/شکست‌خورده را دست
        نمی‌زنیم (ارسال دوباره به زائر، بدتر از ارسال‌نشدن است).
        """
        self.status = SmsStatus.QUEUED
        self.body_redacted = body
        self.context = context_data
        self.error = ""
        self.attempts = attempts
        self.provider = ""
        self.provider_ref = ""
        self.save(
            update_fields=[
                "status",
                "body_redacted",
                "context",
                "error",
                "attempts",
                "provider",
                "provider_ref",
            ]
        )

    @property
    def reference(self) -> uuid.UUID:
        """شناسهٔ رکورد (برای ارجاع در تیکت پشتیبانی)."""
        return self.id
