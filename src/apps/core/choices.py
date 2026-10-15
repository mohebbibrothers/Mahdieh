"""شمارشی‌های مشترک و پایه‌ای هسته."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class Environment(models.TextChoices):
    """محیط اجرای برنامه."""

    DEVELOPMENT = "development", _("توسعه")
    TEST = "test", _("تست")
    STAGING = "staging", _("پیش‌تولید")
    PRODUCTION = "production", _("تولید")


class OutboxStatus(models.TextChoices):
    """وضعیت انتشار یک رخداد در جدول Outbox."""

    PENDING = "pending", _("در انتظار")
    PUBLISHED = "published", _("منتشرشده")
    FAILED = "failed", _("ناموفق")
    DEAD = "dead", _("راکد (نیازمند بررسی)")


class ActorKind(models.TextChoices):
    """نوع کنش‌گر در سابقهٔ حسابرسی."""

    GUEST = "guest", _("زائر")
    OPERATOR = "operator", _("پذیرش")
    MANAGER = "manager", _("مدیر")
    ADMIN = "admin", _("مدیر ارشد")
    SYSTEM = "system", _("سیستم")
