"""مدل‌های صف انتظار — عدالت در دسترسی به سانس‌های آزادشده.

طرح کلی:
    ``WaitlistEntry`` → درخواست یک زائر برای یک پنجرهٔ زمانی (واحد مشخص یا «هر واحد»).
    ``SlotOffer``    → یک پیشنهاد مشخص برای یک سانس آزادشده، با پنجرهٔ پاسخ.

قاعدهٔ کلیدی: برای هر سانس آزادشده **فقط یک پیشنهاد باز** وجود دارد
(``UniqueConstraint`` شرطی). این «پیشنهاد انحصاری» صف را منصفانه می‌کند:
نفر اول فرصت می‌گیرد، اگر نپذیرفت یا پنجره‌اش گذشت، نفر بعدی پیشنهاد می‌گیرد.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.db.fields import conditional_key
from apps.core.models import BaseModel, VersionedModel
from apps.inventory.models import Unit
from apps.waitlist.choices import OfferStatus, WaitlistStatus


class WaitlistEntry(VersionedModel):
    """یک درخواست انتظار برای پنجرهٔ زمانی مشخص.

    پنجرهٔ زمانی همیشه روی شبکهٔ نیم‌روزی است (۸:۰۰ / ۲۰:۰۰) تا با دامنهٔ
    رزرو یکی باشد و هیچ محاسبهٔ موازی‌ای ساخته نشود.
    """

    guest = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="waitlist_entries",
        verbose_name=_("زائر"),
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="waitlist_entries",
        verbose_name=_("واحد دلخواه"),
        help_text=_("خالی یعنی «هر واحدی» — پیشنهاد از هر واحد آزادشده می‌آید."),
    )
    starts_at = models.DateTimeField(db_index=True, verbose_name=_("آغاز پنجرهٔ دلخواه"))
    ends_at = models.DateTimeField(verbose_name=_("پایان پنجرهٔ دلخواه"))
    cells_count = models.PositiveSmallIntegerField(verbose_name=_("تعداد نیم‌روز"))
    guests_count = models.PositiveSmallIntegerField(verbose_name=_("تعداد نفرات"))
    status = models.CharField(
        max_length=16,
        choices=WaitlistStatus.choices,
        default=WaitlistStatus.WAITING,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    position_hint = models.PositiveIntegerField(
        default=0,
        verbose_name=_("نشانهٔ نوبت"),
        help_text=_("در زمان ثبت ذخیره می‌شود؛ نوبت واقعی همیشه از زمان ثبت می‌آید."),
    )
    offers_sent = models.PositiveSmallIntegerField(default=0, verbose_name=_("پیشنهادهای ارسال‌شده"))
    last_offered_at = models.DateTimeField(null=True, blank=True, verbose_name=_("آخرین پیشنهاد"))
    requeued_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان بازگشت به صف"))
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان بسته شدن"))
    converted_reservation = models.OneToOneField(
        "reservation.Reservation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_entry",
        verbose_name=_("رزرو ساخته‌شده"),
    )
    guest_note = models.CharField(
        max_length=200, blank=True, default="", verbose_name=_("یادداشت زائر")
    )
    source = models.CharField(max_length=16, blank=True, default="web", verbose_name=_("منبع ثبت"))
    # یکتایی شرطی «یک درخواست در جریان برای هر زائر» — ر.ک. apps.core.db.fields
    active_guest_key = conditional_key(
        "guest_id",
        Q(status__in=WaitlistStatus.active_statuses()),
        models.UUIDField(null=True),
    )

    class Meta:
        verbose_name = _("درخواست صف انتظار")
        verbose_name_plural = _("صف انتظار")
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=["status", "starts_at"], name="waitlist_status_start_idx"),
            models.Index(fields=["guest", "status"], name="waitlist_guest_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="waitlist_window_ordered",
            ),
            models.CheckConstraint(
                condition=Q(cells_count__gte=1),
                name="waitlist_cells_positive",
            ),
            # یک زائر نمی‌تواند دو درخواست «در جریان» هم‌پوشان داشته باشد؛
            # سهمیهٔ تعداد درخواست‌ها در لایهٔ سرویس اعمال می‌شود.
            models.UniqueConstraint(
                fields=["active_guest_key"], name="one_active_waitlist_entry_per_guest"
            ),
        ]

    def __str__(self) -> str:
        """نمایش خوانا در پنل مدیریت."""
        target = self.unit.code if self.unit is not None else "هر واحد"
        return f"{self.guest.display_name} → {target} ({self.window_label})"

    @property
    def is_active_entry(self) -> bool:
        """آیا این درخواست هنوز در جریان است؟"""
        return self.status in WaitlistStatus.active_statuses()

    @property
    def window_label(self) -> str:
        """برچسب کوتاه پنجرهٔ دلخواه."""
        start = timezone.localtime(self.starts_at)
        end = timezone.localtime(self.ends_at)
        return f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M}"

    @property
    def status_label(self) -> str:
        """برچسب فارسی وضعیت (برای رابط کاربری و گزارش)."""
        return str(WaitlistStatus(self.status).label)

    def matches(self, *, unit_id: Any, start: datetime, end: datetime) -> bool:
        """آیا سانس آزادشده در قالب این درخواست می‌گنجد؟

        قاعده: اگر زائر واحد مشخص خواسته، همان واحد؛ در غیر این صورت هر واحد.
        پنجرهٔ آزادشده باید **کامل** درخواست را پوشش دهد (نه جزئی).
        """
        if self.unit_id and str(self.unit_id) != str(unit_id):
            return False
        return start <= self.starts_at and end >= self.ends_at

    def clean(self) -> None:
        """اعتبارسنجی سطح مدل — هشدار پیش از ذخیره از مسیرهای غیرسرویسی."""
        if self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("پایان پنجره باید بعد از آغاز آن باشد.")})

    def mark(
        self,
        status: str,
        *,
        when: datetime | None = None,
        extra_fields: Sequence[str] = (),
    ) -> None:
        """تغییر وضعیت با ثبت زمان بسته شدن در وضعیت‌های پایانی.

        ``extra_fields`` برای تغییرات همراه است (مثلاً «رزروی که این درخواست به
        آن تبدیل شد»). چرا صریح؟ چون ``save(update_fields=...)`` هر فیلد
        نانوشته را بی‌صدا دور می‌ریزد؛ اگر فهرست این‌جا کامل نباشد، پیوند تبدیل
        در پایگاه داده خالی می‌ماند — خطایی که هیچ‌جا دیده نمی‌شود.
        """
        self.status = status
        if status in WaitlistStatus.terminal_statuses():
            self.resolved_at = when or timezone.now()
        fields = {"status", "resolved_at", "updated_at"} | set(extra_fields)
        self.save(update_fields=sorted(fields))


class SlotOffer(BaseModel):
    """پیشنهاد یک سانس آزادشده به یک درخواست صف.

    ``slot_key`` شناسهٔ متنی سانس است (واحد + آغاز + پایان) تا پیشنهادهای
    رقیبِ یک سانس قابل تشخیص باشند و قاعدهٔ «یک پیشنهاد باز در هر سانس» با
    یک قید یکتایی شرطی تضمین شود.
    """

    entry = models.ForeignKey(
        WaitlistEntry,
        on_delete=models.CASCADE,
        related_name="offers",
        verbose_name=_("درخواست صف"),
    )
    reservation = models.ForeignKey(
        "reservation.Reservation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_offers",
        verbose_name=_("سانس آزادشده (رزرو لغوشده)"),
        help_text=_("رزروی که با لغو/جابه‌جایی/انقضا سانس آن آزاد شد."),
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, related_name="slot_offers", verbose_name=_("واحد")
    )
    starts_at = models.DateTimeField(db_index=True, verbose_name=_("آغاز سانس"))
    ends_at = models.DateTimeField(verbose_name=_("پایان سانس"))
    slot_key = models.CharField(max_length=120, db_index=True, verbose_name=_("شناسهٔ سانس"))
    status = models.CharField(
        max_length=16,
        choices=OfferStatus.choices,
        default=OfferStatus.OPEN,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    expires_at = models.DateTimeField(db_index=True, verbose_name=_("پایان مهلت پاسخ"))
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان پاسخ"))
    decline_reason = models.CharField(
        max_length=32, blank=True, default="", verbose_name=_("دلیل رد")
    )
    created_reservation = models.ForeignKey(
        "reservation.Reservation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offer_acceptances",
        verbose_name=_("رزرو ساخته‌شده از این پیشنهاد"),
    )
    # یکتایی‌های شرطی روی پیشنهاد «باز» — ر.ک. apps.core.db.fields
    open_slot_key = conditional_key(
        "slot_key", Q(status=OfferStatus.OPEN), models.CharField(max_length=120, null=True)
    )
    open_entry_key = conditional_key(
        "entry_id", Q(status=OfferStatus.OPEN), models.UUIDField(null=True)
    )

    class Meta:
        verbose_name = _("پیشنهاد سانس")
        verbose_name_plural = _("پیشنهادهای سانس")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "expires_at"], name="offer_status_expiry_idx")]
        constraints = [
            # یک سانس ⇒ یک پیشنهاد باز (هستهٔ منصفانه بودن صف).
            models.UniqueConstraint(fields=["open_slot_key"], name="one_open_offer_per_slot"),
            # یک درخواست ⇒ یک پیشنهاد باز (زائر هم‌زمان دو انتخاب ندارد).
            models.UniqueConstraint(fields=["open_entry_key"], name="one_open_offer_per_entry"),
        ]

    def __str__(self) -> str:
        """نمایش خوانا در پنل مدیریت."""
        return f"{self.unit.code} · {self.entry.guest.display_name} · {self.status}"

    @property
    def is_open(self) -> bool:
        """آیا پیشنهاد هنوز باز و مهلت‌دار است؟"""
        return self.status == OfferStatus.OPEN and self.expires_at > timezone.now()

    @property
    def seconds_left(self) -> int:
        """ثانیه‌های باقی‌مانده تا پایان مهلت (صفر اگر گذشته باشد)."""
        delta = (self.expires_at - timezone.now()).total_seconds()
        return max(0, int(delta))

    @classmethod
    def key_for(cls, *, unit_id: Any, start: datetime, end: datetime) -> str:
        """ساخت شناسهٔ یکتا برای یک سانس (واحد + بازهٔ زمانی)."""
        return f"{unit_id}:{start.isoformat()}:{end.isoformat()}"
