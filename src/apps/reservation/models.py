"""مدل‌های رزرو، سیاست و تاریخ‌نگاری تصمیم‌ها.

**تضمین مرکزی سیستم (ثابت I1)**: قید ``EXCLUDE USING gist (unit_id WITH =,
block_period WITH &&)`` روی وضعیت‌های فعال. اگر هزار درخواست هم‌زمان برسند،
دیتابیس دقیقاً یکی را می‌پذیرد. هیچ چک نرم‌افزاری جای این قید را نمی‌گیرد و
این قید جای هیچ چک نرم‌افزاری را نمی‌گیرد — هر دو لازم‌اند: یکی برای تجربهٔ
کاربری («چرا نشد؟ چه بازه‌ای آزاد است؟») و یکی برای صحت داده.

تصمیم مستند: در انقضا/لغو، ``block_period`` پاک نمی‌شود. دلیل: قید EXCLUDE
تنها روی وضعیت‌های فعال اعمال می‌شود، پس سانس بلافاصله آزاد است؛ در عوض سابقهٔ
«چه بازه‌ای نگه داشته شده بود» حفظ می‌شود که برای تحلیل و حسابرسی ارزشمند است.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from psycopg.types.range import Range

from apps.core.jalali import format_jalali, weekday_name
from apps.core.models import BaseModel, VersionedModel
from apps.core.uuid7 import uuid7
from apps.identity.models import User
from apps.inventory.lattice import stay_label
from apps.inventory.models import Unit
from apps.reservation.choices import (
    CancelReasonKind,
    ReservationEventKind,
    ReservationSource,
    ReservationStatus,
)

HARD_MIN_CELLS = 1
HARD_MAX_CELLS = 14
CODE_MAX_LENGTH = 20


class Policy(BaseModel):
    """مقدار جاری یک سیاست — داده‌ای که رفتار سیستم را می‌سازد."""

    key = models.CharField(max_length=80, unique=True, verbose_name=_("کلید سیاست"))
    value = models.JSONField(verbose_name=_("مقدار"))
    version = models.PositiveIntegerField(default=1, verbose_name=_("نسخه"))
    effective_from = models.DateTimeField(default=timezone.now, verbose_name=_("معتبر از"))
    effective_to = models.DateTimeField(
        null=True, blank=True, verbose_name=_("معتبر تا"), help_text=_("تهی ⇒ باز و جاری")
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policies_changed",
        verbose_name=_("آخرین تغییردهنده"),
    )
    change_reason = models.CharField(
        max_length=200, blank=True, default="", verbose_name=_("دلیل تغییر")
    )

    class Meta:
        verbose_name = _("سیاست")
        verbose_name_plural = _("سیاست‌ها")
        ordering = ("key",)

    def __str__(self) -> str:
        """نمای خوانا برای پنل مدیریت."""
        return f"{self.key} = {self.value} (v{self.version})"


class PolicyRevision(models.Model):
    """نسخهٔ تغییرناپذیر یک سیاست — تاریخ‌نگاری کامل تصمیم‌های مدیریتی."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False, verbose_name=_("شناسه"))
    policy = models.ForeignKey(
        Policy, on_delete=models.CASCADE, related_name="revisions", verbose_name=_("سیاست")
    )
    version = models.PositiveIntegerField(verbose_name=_("نسخه"))
    value = models.JSONField(verbose_name=_("مقدار"))
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policy_revisions",
        verbose_name=_("تغییردهنده"),
    )
    change_reason = models.CharField(max_length=200, blank=True, default="", verbose_name=_("دلیل"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("زمان ثبت"))

    class Meta:
        verbose_name = _("نسخهٔ سیاست")
        verbose_name_plural = _("نسخه‌های سیاست")
        ordering = ("-created_at",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["policy", "version"], name="uniq_policy_revision_version"
            ),
        ]

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"{self.policy.key} v{self.version}"


class CodeSequence(models.Model):
    """شمارندهٔ کدهای انسان‌خوان رزرو — به‌ازای هر سال جلالی.

    چرا جدول و نه توالی Postgres؟ چون کد باید «M-1405-0231» باشد: خوانا در
    مکالمهٔ تلفنی، کوتاه در پیامک، و هر سال از ۱ شروع شود. قفل ردیفی
    (``select_for_update``) یکتایی را در همزمانی تضمین می‌کند.
    """

    scope = models.CharField(max_length=24, unique=True, verbose_name=_("دامنه (سال جلالی)"))
    last_value = models.PositiveIntegerField(default=0, verbose_name=_("آخرین مقدار"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("زمان ویرایش"))

    class Meta:
        verbose_name = _("شمارندهٔ کد رزرو")
        verbose_name_plural = _("شمارنده‌های کد رزرو")

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"{self.scope}: {self.last_value}"


class Reservation(VersionedModel):
    """رزرو — تعهد میان زائر و زائرسرا برای یک سانس مشخص روی یک واحد مشخص."""

    code = models.CharField(
        max_length=CODE_MAX_LENGTH,
        unique=True,
        verbose_name=_("کد رزرو"),
        help_text=_("کد انسان‌خوان برای مکالمهٔ تلفنی؛ مثل M-1405-0231"),
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, related_name="reservations", verbose_name=_("واحد")
    )
    guest = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="reservations", verbose_name=_("مهمان")
    )
    stay_period = DateTimeRangeField(verbose_name=_("بازهٔ اقامت"))
    block_period = DateTimeRangeField(
        verbose_name=_("بازهٔ مسدودکننده"),
        help_text=_("اقامت + بافر نظافت؛ مبنای قید عدم‌همپوشانی دیتابیس"),
    )
    cells_count = models.PositiveSmallIntegerField(verbose_name=_("تعداد نیم‌روز"))
    guests_count = models.PositiveSmallIntegerField(verbose_name=_("تعداد نفرات"))
    status = models.CharField(
        max_length=24,
        choices=ReservationStatus.choices,
        default=ReservationStatus.CONFIRMED,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    source = models.CharField(
        max_length=12,
        choices=ReservationSource.choices,
        default=ReservationSource.WEB,
        db_index=True,
        verbose_name=_("منبع ایجاد"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations_created",
        verbose_name=_("ایجادکننده (کارمند)"),
    )
    hold_expires_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name=_("انقضای نگه‌داشت")
    )
    guest_note = models.CharField(
        max_length=300, blank=True, default="", verbose_name=_("یادداشت مهمان")
    )
    staff_note = models.CharField(
        max_length=300, blank=True, default="", verbose_name=_("یادداشت داخلی")
    )
    expected_arrival = models.TimeField(null=True, blank=True, verbose_name=_("ساعت تقریبی ورود"))
    vehicle_plate = models.CharField(
        max_length=20, blank=True, default="", verbose_name=_("پلاک خودرو")
    )

    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supersedes",
        verbose_name=_("جانشین"),
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان لغو"))
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations_cancelled",
        verbose_name=_("لغوکننده"),
    )
    cancel_reason_kind = models.CharField(
        max_length=16,
        choices=CancelReasonKind.choices,
        blank=True,
        default="",
        verbose_name=_("دستهٔ دلیل لغو"),
    )
    cancel_reason = models.CharField(
        max_length=300, blank=True, default="", verbose_name=_("دلیل لغو")
    )
    is_late_cancel = models.BooleanField(default=False, verbose_name=_("لغو دیرهنگام"))
    checked_in_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان پذیرش"))
    checked_out_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان تخلیه"))
    no_show_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان ثبت عدم‌حضور"))

    class Meta:
        verbose_name = _("رزرو")
        verbose_name_plural = _("رزروها")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "stay_period"], name="res_status_stay_idx"),
            models.Index(fields=["guest", "status"], name="res_guest_status_idx"),
            models.Index(fields=["unit", "status"], name="res_unit_status_idx"),
            models.Index(fields=["guest", "created_at"], name="res_guest_created_idx"),
            models.Index(fields=["hold_expires_at"], name="res_hold_expiry_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # ── تضمین بنیادین: هیچ دو رزرو فعال روی یک واحد هم‌پوشانی ندارند ──
            ExclusionConstraint(
                name="no_overlapping_active_reservations",
                expressions=[
                    ("unit_id", RangeOperators.EQUAL),
                    ("block_period", RangeOperators.OVERLAPS),
                ],
                condition=Q(
                    status__in=[
                        ReservationStatus.HOLD,
                        ReservationStatus.CONFIRMED,
                        ReservationStatus.CHECKED_IN,
                    ]
                ),
            ),
            models.CheckConstraint(
                condition=Q(status__in=ReservationStatus.values),
                name="reservation_status_known",
            ),
            models.CheckConstraint(
                condition=Q(guests_count__gte=1) & Q(guests_count__lte=60),
                name="reservation_guests_within_bounds",
            ),
            models.CheckConstraint(
                condition=Q(cells_count__gte=HARD_MIN_CELLS) & Q(cells_count__lte=HARD_MAX_CELLS),
                name="reservation_cells_within_bounds",
            ),
            models.CheckConstraint(
                condition=Q(block_period__contains=F("stay_period")),
                name="reservation_block_covers_stay",
            ),
            models.CheckConstraint(
                condition=Q(stay_period__isempty=False) & Q(block_period__isempty=False),
                name="reservation_periods_not_empty",
            ),
        ]

    def __str__(self) -> str:
        """نمای خوانا — کد رزرو، واحد و وضعیت."""
        return f"{self.code} · {self.unit.code} · {self.get_status_display()}"

    # ─── ویژگی‌ها ─────────────────────────────────────────────────────────────
    @property
    def starts_at(self) -> datetime:
        """آغاز بازهٔ اقامت."""
        return self.stay_period.lower  # type: ignore[no-any-return]

    @property
    def ends_at(self) -> datetime:
        """پایان بازهٔ اقامت."""
        return self.stay_period.upper  # type: ignore[no-any-return]

    @property
    def stay_range(self) -> Range[datetime]:
        """بازهٔ اقامت به‌صورت ``Range`` (برای کوئری‌های هم‌پوشانی)."""
        return Range(self.starts_at, self.ends_at)

    @property
    def is_active_reservation(self) -> bool:
        """آیا این رزرو سانس را اشغال می‌کند؟"""
        return self.status in ReservationStatus.active_statuses()

    @property
    def is_terminal(self) -> bool:
        """آیا رزرو در وضعیت پایانی است؟"""
        return self.status in ReservationStatus.terminal_statuses()

    @property
    def nights(self) -> int:
        """تعداد شب‌ها (هر شبانه‌روز = ۲ نیم‌روز)."""
        return self.cells_count // 2

    @property
    def duration_label(self) -> str:
        """برچسب فارسی مدت اقامت."""
        return stay_label(self.cells_count)

    @property
    def stay_date_jalali(self) -> str:
        """تاریخ جلالی آغاز اقامت."""
        return format_jalali(timezone.localtime(self.starts_at).date(), numeric=True)

    @property
    def start_weekday(self) -> str:
        """نام روز هفتهٔ آغاز اقامت (فارسی)."""
        return weekday_name(timezone.localtime(self.starts_at).date())

    @property
    def expected_arrival_time(self) -> time | None:
        """ساعت تقریبی ورود (اگر ثبت شده باشد)."""
        return self.expected_arrival

    def status_label(self) -> str:
        """برچسب فارسی وضعیت — برای گزارش‌ها و پیامک."""
        return str(ReservationStatus(self.status).label)

    def publish_event(
        self,
        kind: str,
        *,
        from_status: str = "",
        to_status: str = "",
        actor: User | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ReservationEvent:
        """ثبت یک رخداد در تاریخ زندگی رزرو (بدون انتشار بیرونی)."""
        return ReservationEvent.objects.create(
            reservation=self,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            actor_label=actor.display_name if actor else "سیستم",
            reason=reason,
            metadata=metadata or {},
        )


class ReservationEvent(models.Model):
    """رخداد رزرو — فقط افزودنی؛ سابقه هرگز ویرایش یا حذف نمی‌شود."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False, verbose_name=_("شناسه"))
    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name="events", verbose_name=_("رزرو")
    )
    kind = models.CharField(
        max_length=16,
        choices=ReservationEventKind.choices,
        db_index=True,
        verbose_name=_("نوع رخداد"),
    )
    from_status = models.CharField(
        max_length=24, blank=True, default="", verbose_name=_("از وضعیت")
    )
    to_status = models.CharField(max_length=24, blank=True, default="", verbose_name=_("به وضعیت"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservation_events",
        verbose_name=_("کنش‌گر"),
    )
    actor_label = models.CharField(
        max_length=80, blank=True, default="", verbose_name=_("برچسب کنش‌گر")
    )
    reason = models.CharField(max_length=300, blank=True, default="", verbose_name=_("دلیل"))
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_("فراداده"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("زمان"))

    class Meta:
        verbose_name = _("رخداد رزرو")
        verbose_name_plural = _("رخدادهای رزرو")
        ordering = ("created_at",)

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"{self.reservation.code} · {self.get_kind_display()}"
