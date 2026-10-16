"""فرم‌های وب — دروازهٔ اعتبارسنجی پیش از رسیدن داده به سرویس‌های دامنه.

قاعده: فرم‌ها فقط «شکل ورودی» را می‌سنجند و بازهٔ زمانی را با ریاضیات
``lattice`` می‌سازند. هر قاعدهٔ دامنه‌ای (سهمیه، تقاضا، تداخل) در سرویس
اعمال می‌شود — یک منبع حقیقت، دو لایهٔ پیام خطا.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Final, cast

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from apps.identity.choices import Province
from apps.identity.validators import normalize_phone, validate_national_id
from apps.inventory.choices import BlockKind
from apps.inventory.lattice import (
    LatticeConfig,
    cell_index,
    cells_for_day,
    local_zone,
    period_from_index,
)
from apps.inventory.models import Unit
from apps.inventory.selectors import active_units
from apps.reservation.choices import CancelReasonKind
from apps.reservation.policy import lattice_config, policy_int

PERIOD_DAY: Final[str] = "day"
PERIOD_NIGHT: Final[str] = "night"
PERIOD_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    (PERIOD_DAY, "روز — ۰۸:۰۰ تا ۲۰:۰۰"),
    (PERIOD_NIGHT, "شب — ۲۰:۰۰ تا ۰۸:۰۰ صبح روز بعد"),
)
MIN_REASON_LENGTH: Final[int] = 5


def period_offset(day: date, period: str, config: LatticeConfig) -> int:
    """شمارهٔ سلول آغاز برای «روز» یا «شب» در یک تاریخ.

    عدد جادویی نوشته نمی‌شود: ترتیب سلول‌ها از خودِ ``lattice`` خوانده می‌شود
    تا اگر روزی ساعت شروع شبکه تغییر کرد، این نقطه هم بی‌صدا اشتباه نکند.
    """
    for cell in cells_for_day(day, config):
        if cell.kind == period:
            return cell.index - cell_index(day, 0, config)
    msg = "بازهٔ انتخابی در شبکهٔ نیم‌روز وجود ندارد."
    raise DjangoValidationError(msg)


def resolve_period(day: date, period: str, cells: int) -> tuple[datetime, datetime]:
    """ساخت بازهٔ ``(start, end)`` از تاریخ، نوع سانс و تعداد نیم‌روز."""
    config = lattice_config()
    offset = period_offset(day, period, config)
    start_index = cell_index(day, offset, config)
    return period_from_index(start_index, cells, config)


class PhoneForm(forms.Form):
    """گام نخست ورود — شمارهٔ تلفن."""

    phone = forms.CharField(
        label="شمارهٔ تلفن همراه",
        max_length=16,
        widget=forms.TextInput(
            attrs={
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "۰۹۱۲۰۰۰۰۰۰۰",
                "dir": "ltr",
                "class": "field-input",
            }
        ),
    )
    next = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_phone(self) -> str:
        """نرمال‌سازی شماره؛ پذیرش قالب‌های ۰۹…، +۹۸…، ۹۸… و ارقام فارسی."""
        raw = str(self.cleaned_data["phone"])
        try:
            return normalize_phone(raw)
        except DjangoValidationError as exc:
            raise forms.ValidationError(getattr(exc, "messages", [str(exc)])[0]) from exc


class OtpForm(forms.Form):
    """گام دوم ورود — کد یک‌بارمصرف."""

    phone = forms.CharField(widget=forms.HiddenInput())
    code = forms.CharField(
        label="کد پنج‌رقمی",
        min_length=5,
        max_length=5,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "— — — — —",
                "dir": "ltr",
                "class": "field-input otp-input",
                "maxlength": "5",
            }
        ),
    )
    next = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_code(self) -> str:
        """پذیرش ارقام فارسی و اطمینان از عددی‌بودن کد."""
        from apps.identity.validators import normalize_digits

        code = normalize_digits(str(self.cleaned_data["code"]).strip())
        if not code.isdigit():
            raise forms.ValidationError("کد فقط باید رقم باشد.")
        return code


class ProfileForm(forms.Form):
    """تکمیل پروفایل زائر — پیش‌نیاز رزرو (ثابت I5)."""

    first_name = forms.CharField(label="نام", max_length=50)
    last_name = forms.CharField(label="نام خانوادگی", max_length=50)
    national_id = forms.CharField(
        label="کد ملی",
        max_length=10,
        # autocomplete=off: کد ملی نباید در حافظهٔ تکمیل خودکار مرورگر بماند
        widget=forms.TextInput(attrs={"inputmode": "numeric", "dir": "ltr", "autocomplete": "off"}),
    )
    province = forms.ChoiceField(label="استان", choices=Province.choices)
    city = forms.CharField(label="شهر", max_length=60)
    next = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_national_id(self) -> str:
        """اعتبارسنجی کد ملی با الگوریتم رسمی (رقم کنترل)."""
        raw = str(self.cleaned_data["national_id"])
        try:
            return validate_national_id(raw)
        except DjangoValidationError as exc:
            raise forms.ValidationError(getattr(exc, "messages", [str(exc)])[0]) from exc


class BookingForm(forms.Form):
    """فرم رزرو — تاریخ، سانس و تعداد نیم‌روز؛ همه از شبکهٔ رسمی."""

    unit_code = forms.CharField(max_length=12)
    day = forms.DateField(
        label="تاریخ ورود",
        widget=forms.DateInput(attrs={"type": "date", "class": "field-input", "dir": "ltr"}),
    )
    period = forms.ChoiceField(label="سانس", choices=PERIOD_CHOICES, initial=PERIOD_DAY)
    cells = forms.IntegerField(label="تعداد نیم‌روز", min_value=1)
    guests_count = forms.IntegerField(label="تعداد نفرات", min_value=1)
    expected_arrival = forms.TimeField(label="ساعت تقریبی ورود", required=False)
    vehicle_plate = forms.CharField(label="شمارهٔ خودرو", max_length=16, required=False)
    guest_note = forms.CharField(label="یادداشت برای پذیرش", max_length=280, required=False)
    idempotency_key = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """بارگذاری سقف سیاست‌ها در زمان ساخت فرم (نه در زمان import)."""
        super().__init__(*args, **kwargs)
        cell_field = cast(forms.IntegerField, self.fields["cells"])
        cell_field.max_value = policy_int("reservation.max_cells")
        horizon = policy_int("reservation.booking_horizon_days")
        self.fields["day"].widget.attrs["max"] = (
            timezone.localdate() + timedelta(days=horizon)
        ).isoformat()

    def clean_unit_code(self) -> str:
        """واحد باید وجود داشته باشد و فعال باشد."""
        code = str(self.cleaned_data["unit_code"]).strip().upper()
        if not active_units().filter(code=code).exists():
            raise forms.ValidationError("این واحد وجود ندارد یا در حال حاضر فعال نیست.")
        return code

    def clean(self) -> dict[str, Any]:
        """ساخت بازهٔ نهایی و بررسی مرزهای سادهٔ زمانی."""
        cleaned = super().clean() or {}
        if self.errors:
            return cleaned
        day: date = cleaned["day"]
        cells: int = cleaned["cells"]
        period: str = cleaned["period"]
        start, end = resolve_period(day, period, cells)
        now = timezone.now()
        if end <= now:
            raise forms.ValidationError("بازهٔ انتخابی تمام شده است؛ تاریخ دیگری انتخاب کنید.")
        if start <= now:
            raise forms.ValidationError(
                "این بازه پیش از «مهلت آماده‌سازی» آغاز می‌شود؛ سانس بعدی را انتخاب کنید."
            )
        cleaned["start"] = start
        cleaned["end"] = end
        return cleaned

    @property
    def unit(self) -> Unit | None:
        """واحد انتخاب‌شده (پس از اعتبارسنجی)."""
        code = self.cleaned_data.get("unit_code") if self.is_valid() else None
        return active_units().filter(code=code).first() if code else None


class CancelForm(forms.Form):
    """لغو رزرو توسط زائر."""

    expected_version = forms.IntegerField(min_value=1)
    reason_kind = forms.ChoiceField(
        label="دلیل",
        choices=[
            (CancelReasonKind.GUEST_REQUEST, "درخواست خودم"),
            (CancelReasonKind.OTHER, "سایر (توضیح می‌دهم)"),
        ],
        initial=CancelReasonKind.GUEST_REQUEST,
    )
    reason = forms.CharField(label="توضیح (اختیاری)", max_length=200, required=False)


class StaffCancelForm(CancelForm):
    """لغو رزرو توسط کارکنان — دلیل اجباری و دسته‌های عملیاتی."""

    reason_kind = forms.ChoiceField(label="دلیل", choices=CancelReasonKind.choices)
    reason = forms.CharField(label="توضیح دلیل (اجباری)", max_length=200)

    def clean_reason(self) -> str:
        """دلیل کارکنان باید معنادار باشد (حداقل ۵ نویسه)."""
        reason = str(self.cleaned_data["reason"]).strip()
        if len(reason) < MIN_REASON_LENGTH:
            raise forms.ValidationError("دلیل باید حداقل ۵ نویسه باشد.")
        return reason


class MoveForm(forms.Form):
    """جابه‌جایی رزرو به واحد/زمان دیگر (کارکنان) — با دلیل اجباری."""

    unit_code = forms.CharField(max_length=12)
    day = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "dir": "ltr"}))
    period = forms.ChoiceField(choices=PERIOD_CHOICES)
    cells = forms.IntegerField(min_value=1)
    expected_version = forms.IntegerField(min_value=1)
    reason = forms.CharField(max_length=200)

    def clean_unit_code(self) -> str:
        """واحد مقصد باید فعال باشد."""
        code = str(self.cleaned_data["unit_code"]).strip().upper()
        if not active_units().filter(code=code).exists():
            raise forms.ValidationError("واحد مقصد معتبر نیست.")
        return code

    def clean_reason(self) -> str:
        """دلیل جابه‌جایی اجباری است (قاعدهٔ I14)."""
        reason = str(self.cleaned_data["reason"]).strip()
        if len(reason) < MIN_REASON_LENGTH:
            raise forms.ValidationError("دلیل جابه‌جایی اجباری است.")
        return reason

    def clean(self) -> dict[str, Any]:
        """ساخت بازهٔ مقصد از شبکهٔ نیم‌روز."""
        cleaned = super().clean() or {}
        if self.errors:
            return cleaned
        start, end = resolve_period(cleaned["day"], cleaned["period"], cleaned["cells"])
        cleaned["start"] = start
        cleaned["end"] = end
        return cleaned


class BlockForm(forms.Form):
    """ساخت بلوک نگهداری/نظافت — سراسری یا برای یک واحد."""

    unit_code = forms.CharField(
        label="واحد",
        max_length=12,
        required=False,
        help_text="خالی بگذارید تا بلوک سراسری ساخته شود.",
    )
    start_local = forms.DateTimeField(
        label="آغاز",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "dir": "ltr", "class": "field-input"}
        ),
    )
    end_local = forms.DateTimeField(
        label="پایان",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "dir": "ltr", "class": "field-input"}
        ),
    )
    kind = forms.ChoiceField(label="نوع", choices=BlockKind.choices, initial=BlockKind.MAINTENANCE)
    reason = forms.CharField(label="دلیل (اجباری)", max_length=200)

    def clean_reason(self) -> str:
        """دلیل بلوک اجباری است و باید معنادار باشد."""
        reason = str(self.cleaned_data["reason"]).strip()
        if len(reason) < MIN_REASON_LENGTH:
            raise forms.ValidationError("دلیل بلوک باید حداقل ۵ نویسه باشد.")
        return reason

    def clean_unit_code(self) -> str:
        """کد واحد اختیاری است؛ اگر آمد، باید معتبر باشد."""
        code = str(self.cleaned_data.get("unit_code") or "").strip().upper()
        if code and not active_units().filter(code=code).exists():
            raise forms.ValidationError("واحد انتخابی معتبر نیست.")
        return code

    def clean(self) -> dict[str, Any]:
        """آگاه‌سازی زمان‌ها با منطقهٔ محلی و بررسی ترتیب."""
        cleaned = super().clean() or {}
        if self.errors:
            return cleaned
        start = _as_local(cleaned["start_local"])
        end = _as_local(cleaned["end_local"])
        if end <= start:
            raise forms.ValidationError("پایان بلوک باید بعد از آغاز آن باشد.")
        cleaned["start"] = start
        cleaned["end"] = end
        return cleaned


class ExportForm(forms.Form):
    """فرم خروجی اکسل — بازهٔ تاریخ و واحدهای دلخواه."""

    from_date = forms.DateField(
        label="از تاریخ",
        widget=forms.DateInput(attrs={"type": "date", "dir": "ltr", "class": "field-input"}),
    )
    to_date = forms.DateField(
        label="تا تاریخ",
        widget=forms.DateInput(attrs={"type": "date", "dir": "ltr", "class": "field-input"}),
    )
    units = forms.MultipleChoiceField(label="واحدها", required=False, choices=())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """پرکردن گزینه‌های واحد فعال + پیش‌فرض دو هفتهٔ آینده."""
        super().__init__(*args, **kwargs)
        self.fields["units"].choices = [  # type: ignore[attr-defined]
            (unit.code, f"{unit.code} — {unit.title}") for unit in active_units()
        ]
        today = timezone.localdate()
        self.fields["from_date"].initial = today
        self.fields["to_date"].initial = today + timedelta(days=14)

    def clean(self) -> dict[str, Any]:
        """ترتیب بازه و سقف ۳۶۶ روز (هم‌راستا با قرارداد گزارش‌گیری)."""
        cleaned = super().clean() or {}
        if self.errors:
            return cleaned
        if cleaned["to_date"] < cleaned["from_date"]:
            raise forms.ValidationError("بازهٔ تاریخ نامعتبر است: «تا» باید بعد از «از» باشد.")
        return cleaned


def _as_local(moment: datetime) -> datetime:
    """آگاه‌سازی یک داتایم بی‌منطقه با منطقهٔ محلی زائرسرا."""
    if timezone.is_naive(moment):
        return timezone.make_aware(moment, local_zone())
    return moment.astimezone(local_zone())


def default_block_window() -> dict[str, str]:
    """پنجرهٔ پیش‌فرض فرم بلوک — فردا ۰۸:۰۰ تا ۲۰:۰۰."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = datetime.combine(tomorrow, time(hour=8))
    end = datetime.combine(tomorrow, time(hour=20))
    return {"start": start.strftime("%Y-%m-%dT%H:%M"), "end": end.strftime("%Y-%m-%dT%H:%M")}
