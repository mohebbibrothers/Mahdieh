"""تنها دادهٔ اپ وب: بازخورد بی‌نام «این پاسخ مفید بود؟» در راهنمای زائر.

چرا اینجا؟ محتوای راهنما در همین اپ است (``help_content``)؛ بازخوردش هم کنار همان می‌ماند.
حریم خصوصی: هیچ شناسه‌ای از زائر (کاربر، IP، دستگاه) ذخیره نمی‌شود — فقط «کدام پرسش، مفید
بود یا نه، چه زمانی». سوءاستفاده با سقف درخواست API مهار می‌شود، نه با ردگیری فرد.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class HelpVote(models.Model):
    """یک رأی بی‌نام دربارهٔ یک پاسخ راهنما."""

    slug = models.SlugField("پرسش", max_length=64)
    helpful = models.BooleanField("مفید بود؟")
    created_at = models.DateTimeField("زمان", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "بازخورد راهنما"
        verbose_name_plural = "بازخوردهای راهنما"
        ordering = ("-created_at",)
        indexes = (models.Index(fields=("slug", "created_at"), name="web_helpvote_slug_time"),)

    def __str__(self) -> str:
        """نمایش کوتاه در پنل."""
        return f"{self.slug} · {'مفید' if self.helpful else 'نامفید'}"
