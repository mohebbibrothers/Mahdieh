"""نماهای عمومی سایت مهدیه — رندر سمت سرور با تقویت HTMX.

قواعد این لایه:
    • **نازک**: هیچ قاعدهٔ دامنه‌ای اینجا بازنویسی نمی‌شود؛ فقط سرویس و پرسمان.
    • **بدون نوشتن مستقیم**: هر تغییر از سرویس‌های دامنه می‌گذرد (تست معماری).
    • **دو حالت یک نما**: صفحهٔ کامل برای مرورگر، قطعهٔ HTML برای HTMX — یک منبع
      داده، دو قالب؛ هیچ منطقی دو بار نوشته نمی‌شود.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final
import uuid

from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView, View

from apps.core.errors import DomainError
from apps.core.idempotency import execute_idempotent
from apps.identity import services as identity_services
from apps.inventory import selectors as inventory_selectors
from apps.reservation import selectors as reservation_selectors, services as reservation_services
from apps.reservation.availability import MAX_MATRIX_DAYS, availability_matrix
from apps.reservation.certificate import (
    build_certificate,
    certificate_qr_svg,
    certificate_verify_url,
)
from apps.reservation.policy import policy_int
from apps.web import presenters
from apps.web.forms import BookingForm, CancelForm, OtpForm, PhoneForm, ProfileForm
from apps.web.mixins import DashboardLoginMixin, ProfileRequiredMixin, current_user

SESSION_BACKEND: Final[str] = "django.contrib.auth.backends.ModelBackend"
HOME_WINDOW_DAYS: Final[int] = 7
UNIT_WINDOW_DAYS: Final[int] = 10
CALENDAR_DEFAULT_DAYS: Final[int] = 14
MAX_QUOTE_ALTERNATIVES: Final[int] = 3
DEFAULT_GUESTS: Final[int] = 2
BOOKING_SCOPE: Final[str] = "web.booking"
QUOTE_FIELD_NAMES: Final[tuple[str, ...]] = (
    "unit_code",
    "day",
    "period",
    "cells",
    "guests_count",
)


def safe_next(request: HttpRequest, candidate: str = "") -> str:
    """مقصد بازگشت امن — فقط مسیرهای داخلی همین سایت (ضد open-redirect)."""
    target = candidate or request.POST.get("next", "") or request.GET.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return ""


def parse_day(raw: str | None, *, default_offset: int = 0) -> date:
    """تفسیر تاریخ ورودی؛ در صورت خطا، تاریخ پیش‌فرض محلی."""
    fallback = timezone.localdate() + timedelta(days=default_offset)
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return fallback


def clamp_days(raw: str | None, *, default: int = CALENDAR_DEFAULT_DAYS) -> int:
    """تعداد روزهای تقویم را در بازهٔ مجاز نگه می‌دارد."""
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_MATRIX_DAYS))


class CalendarContextMixin:
    """ساخت زمینهٔ مشترک تقویم — یک محاسبه، دو مصرف (صفحهٔ کامل و قطعهٔ HTMX)."""

    request: HttpRequest

    def calendar_context(self) -> dict[str, Any]:
        """زمینهٔ تقویم از پارامترهای درخواست (تاریخ، طول بازه، فیلتر واحد)."""
        start_day = parse_day(self.request.GET.get("from"))
        days = clamp_days(self.request.GET.get("days"))
        unit_code = (self.request.GET.get("unit") or "").strip().upper()
        matrix = availability_matrix(start_day=start_day, days=days, use_cache=True)
        rows = presenters.calendar_rows(matrix)
        if unit_code:
            rows = [row for row in rows if row.code == unit_code]
        return {
            "headings": presenters.day_headings(matrix),
            "rows": rows,
            "stats": presenters.matrix_stats(matrix),
            "start_day": start_day,
            "days": days,
            "prev_day": (start_day - timedelta(days=days)).isoformat(),
            "next_day": (start_day + timedelta(days=days)).isoformat(),
            "unit_code": unit_code,
            "units": list(inventory_selectors.active_units()),
            "today": timezone.localdate(),
            "legend": presenters.CELL_HINTS,
        }


class HomeView(TemplateView):
    """صفحهٔ خانه — معرفی زائرسرا، وضعیت واقعی روزهای آینده و مسیر رزرو."""

    template_name = "web/home.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ صفحهٔ خانه از دادهٔ زندهٔ دیتابیس (هیچ عدد ساختگی)."""
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        units = list(inventory_selectors.active_units())
        matrix = availability_matrix(start_day=today, days=HOME_WINDOW_DAYS, use_cache=True)
        context.update(
            {
                "today": today,
                "units": units,
                "featured_units": units[:3],
                "floors": presenters.units_by_floor(units),
                "stats": presenters.matrix_stats(matrix),
                "headings": presenters.day_headings(matrix),
                "rows": presenters.calendar_rows(matrix),
                "occasions": presenters.upcoming_occasions(
                    inventory_selectors.occasions_between(today, today + timedelta(days=45))
                ),
                "max_cells": policy_int("reservation.max_cells"),
                "min_cells": policy_int("reservation.min_cells"),
                "horizon_days": policy_int("reservation.booking_horizon_days"),
                "window_days": HOME_WINDOW_DAYS,
            }
        )
        return context


class UnitListView(TemplateView):
    """فهرست واحدهای اقامتی با وضعیت واقعی روزهای آینده."""

    template_name = "web/units.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """واحدها به‌تفکیک طبقه + شمار نیم‌روزهای آزاد هر واحد."""
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        matrix = availability_matrix(start_day=today, days=UNIT_WINDOW_DAYS, use_cache=True)
        rows = presenters.calendar_rows(matrix)
        context.update(
            {
                "floors": presenters.units_by_floor(list(inventory_selectors.active_units())),
                "free_by_unit": {row.code: row.free_cells for row in rows},
                "window_days": UNIT_WINDOW_DAYS,
                # قطعهٔ تقویم به همین کلیدها نیاز دارد؛ منبع داده یکی است.
                "headings": presenters.day_headings(matrix),
                "rows": rows,
                "stats": presenters.matrix_stats(matrix),
                "start_day": today,
                "days": UNIT_WINDOW_DAYS,
                "unit_code": "",
                "today": today,
            }
        )
        return context


class UnitDetailView(TemplateView):
    """صفحهٔ یک واحد — امکانات، ظرفیت و تقویم اختصاصی همان واحد."""

    template_name = "web/unit_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """اطلاعات واحد از کد مسیر؛ ۴۰۴ در صورت نبود یا غیرفعال بودن."""
        context = super().get_context_data(**kwargs)
        unit = inventory_selectors.unit_by_code(str(kwargs["code"]))
        if unit is None:
            raise Http404("واحد مورد نظر یافت نشد.")
        today = timezone.localdate()
        matrix = availability_matrix(start_day=today, days=UNIT_WINDOW_DAYS, use_cache=True)
        rows = [row for row in presenters.calendar_rows(matrix) if row.code == unit.code]
        context.update(
            {
                "unit": unit,
                "features": unit.features.all(),
                "headings": presenters.day_headings(matrix),
                "rows": rows,
                "row": rows[0] if rows else None,
                "window_days": UNIT_WINDOW_DAYS,
                "max_cells": policy_int("reservation.max_cells"),
                "min_cells": policy_int("reservation.min_cells"),
                "stats": presenters.matrix_stats(matrix),
                "start_day": today,
                "days": UNIT_WINDOW_DAYS,
                "unit_code": unit.code,
                "today": today,
            }
        )
        return context


class CalendarView(CalendarContextMixin, TemplateView):
    """صفحهٔ تقویم موجودی — جدول «واحد × نیم‌روز» برای یک بازه."""

    template_name = "web/calendar.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ کامل تقویم."""
        context = super().get_context_data(**kwargs)
        context.update(self.calendar_context())
        return context


class CalendarGridPartialView(CalendarContextMixin, TemplateView):
    """قطعهٔ HTMX تقویم — ناوبری بازه‌ها بدون بارگذاری کامل صفحه."""

    template_name = "web/partials/calendar_grid.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """همان دادهٔ صفحهٔ تقویم، بدون پوسته."""
        context = super().get_context_data(**kwargs)
        context.update(self.calendar_context())
        return context


class LoginView(TemplateView):
    """ورود با شمارهٔ تلفن — گام ۱: ارسال کد، گام ۲: تأیید کد."""

    template_name = "web/login.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ گام نخست (یا گام کد اگر در ورودی آمده باشد)."""
        context = super().get_context_data(**kwargs)
        context.setdefault("step", "phone")
        context.setdefault("phone_form", PhoneForm())
        context["next"] = self.request.GET.get("next", "")
        context["max_attempts"] = identity_services.otp_max_attempts()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """درخواست کد؛ پاسخ برای شماره‌های ثبت‌شده و ثبت‌نشده یکسان است."""
        form = PhoneForm(request.POST)
        target = request.POST.get("next", "")
        if not form.is_valid():
            context = self.get_context_data()
            context.update({"step": "phone", "phone_form": form, "next": target})
            return self.render_to_response(context)
        phone = str(form.cleaned_data["phone"])
        try:
            result = identity_services.request_otp(phone=phone, request=request)
        except DomainError as error:
            messages.error(request, error.detail)
            context = self.get_context_data()
            context.update({"step": "phone", "phone_form": form, "next": target})
            return self.render_to_response(context)
        context = self.get_context_data()
        context.update(
            {
                "step": "code",
                "phone": phone,
                "otp_form": OtpForm(initial={"phone": phone, "next": target}),
                "expires_in": result.expires_in,
                "resend_after": result.resend_after,
                "delivery_status": result.delivery_status,
                "next": target,
            }
        )
        return self.render_to_response(context)


class LoginVerifyView(TemplateView):
    """گام دوم ورود — تأیید کد یک‌بارمصرف و باز کردن نشست."""

    template_name = "web/login.html"

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """تأیید کد؛ موفقیت ⇒ هدایت به مقصد، شکست ⇒ بازنمایش با پیام روشن."""
        form = OtpForm(request.POST)
        target = request.POST.get("next", "")
        if not form.is_valid():
            return self._rerender(request, form, target, "شمارهٔ تلفن یا قالب کد نامعتبر است.")
        try:
            identity = identity_services.verify_otp(
                phone=str(form.cleaned_data["phone"]),
                code=str(form.cleaned_data["code"]),
                request=request,
            )
        except DomainError as error:
            return self._rerender(request, form, target, error.detail)
        auth_login(request, identity.user, backend=SESSION_BACKEND)
        destination = safe_next(request, target)
        if not identity.user.is_profile_complete:
            profile_url = reverse("web:profile")
            return redirect(f"{profile_url}?next={destination}" if destination else profile_url)
        messages.success(request, f"خوش آمدید، {identity.user.display_name}.")
        return redirect(destination or reverse("web:my_reservations"))

    def _rerender(
        self, request: HttpRequest, form: OtpForm, target: str, error: str
    ) -> HttpResponse:
        """بازنمایش گام کد با پیام خطا و شمارهٔ حفظ‌شده (بدون افشای جزئیات)."""
        messages.error(request, error)
        phone = str(request.POST.get("phone", ""))
        return self.render_to_response(
            {
                "step": "code",
                "phone": phone,
                "otp_form": OtpForm(initial={"phone": phone, "next": target}),
                "next": target,
                "max_attempts": identity_services.otp_max_attempts(),
            }
        )


class LogoutView(View):
    """بستن نشست — فقط با POST (قاعدهٔ ضد CSRF)."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """پایان نشست و بازگشت به صفحهٔ خانه."""
        auth_logout(request)
        messages.success(request, "از حساب خود خارج شدید.")
        return redirect("web:home")


class ProfileView(DashboardLoginMixin, TemplateView):
    """تکمیل و ویرایش پروفایل زائر — پیش‌نیاز رزرو (ثابت I5)."""

    template_name = "web/profile.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """فرم پیش‌پرشده با دادهٔ فعلی کاربر."""
        context = super().get_context_data(**kwargs)
        context.setdefault("form", ProfileForm(initial=self._initial()))
        context["next"] = self.request.GET.get("next", "")
        context["profile_complete"] = current_user(self.request).is_profile_complete
        return context

    def _initial(self) -> dict[str, Any]:
        """مقادیر آغازین فرم از پروفایل کاربر (کد ملی رمزگشایی‌شده)."""
        user = current_user(self.request)
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "province": user.province,
            "city": user.city,
            "national_id": user.reveal_national_id(),
        }

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """ذخیرهٔ پروفایل از طریق سرویس دامنه و هدایت به مقصد درخواست."""
        form = ProfileForm(request.POST)
        target = request.POST.get("next", "")
        if not form.is_valid():
            context = self.get_context_data()
            context.update({"form": form, "next": target})
            return self.render_to_response(context)
        try:
            identity_services.complete_profile(
                user=current_user(request),
                first_name=str(form.cleaned_data["first_name"]),
                last_name=str(form.cleaned_data["last_name"]),
                province=str(form.cleaned_data["province"]),
                city=str(form.cleaned_data["city"]),
                national_id=str(form.cleaned_data["national_id"]),
                request=request,
            )
        except DomainError as error:
            messages.error(request, error.detail)
            context = self.get_context_data()
            context.update({"form": form, "next": target})
            return self.render_to_response(context)
        messages.success(request, "پروفایل شما کامل شد؛ حالا می‌توانید رزرو کنید.")
        return redirect(safe_next(request, target) or reverse("web:my_reservations"))


class BookView(ProfileRequiredMixin, TemplateView):
    """فرم رزرو — انتخاب سانس، تعداد نیم‌روز و ثبت نهایی."""

    template_name = "web/book.html"

    def unit(self) -> Any:
        """واحد مورد نظر از کد مسیر؛ ۴۰۴ در صورت نبود."""
        unit = inventory_selectors.unit_by_code(str(self.kwargs["code"]))
        if unit is None:
            raise Http404("واحد مورد نظر یافت نشد.")
        return unit

    def initial_data(self, unit: Any) -> dict[str, Any]:
        """مقادیر آغازین فرم از پارامترهای آدرس یا پیش‌فرض سیاست‌ها."""
        return {
            "unit_code": unit.code,
            "day": parse_day(self.request.GET.get("day"), default_offset=1).isoformat(),
            "period": self.request.GET.get("period") or "day",
            "cells": self.request.GET.get("cells") or policy_int("reservation.min_cells"),
            "guests_count": self.request.GET.get("guests") or DEFAULT_GUESTS,
            "idempotency_key": uuid.uuid4().hex,
        }

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ فرم رزرو."""
        context = super().get_context_data(**kwargs)
        unit = self.unit()
        context.update(
            {
                "unit": unit,
                "features": unit.features.all(),
                "form": kwargs.get("form") or BookingForm(initial=self.initial_data(unit)),
                "max_cells": policy_int("reservation.max_cells"),
                "min_cells": policy_int("reservation.min_cells"),
                "horizon_days": policy_int("reservation.booking_horizon_days"),
                "current_reservation": reservation_selectors.reservations_for_user(
                    current_user(self.request)
                ).first(),
            }
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """ثبت رزرو با کلید هم‌توانی — دوبار زدن دکمه، دو رزرو نمی‌سازد."""
        unit = self.unit()
        form = BookingForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        key = str(form.cleaned_data.get("idempotency_key") or uuid.uuid4().hex)
        payload = {name: str(form.cleaned_data[name]) for name in QUOTE_FIELD_NAMES}

        def action() -> tuple[int, Any]:
            """ساخت رزرو در سرویس دامنه و برگرداندن کد پیگیری."""
            reservation = reservation_services.create_reservation(
                guest=current_user(request),
                unit=unit,
                start=form.cleaned_data["start"],
                end=form.cleaned_data["end"],
                guests_count=int(form.cleaned_data["guests_count"]),
                actor=current_user(request),
                guest_note=str(form.cleaned_data.get("guest_note") or ""),
                expected_arrival=form.cleaned_data.get("expected_arrival"),
                vehicle_plate=str(form.cleaned_data.get("vehicle_plate") or ""),
                request=request,
            )
            return 201, {"code": reservation.code}

        try:
            outcome = execute_idempotent(
                key=key,
                scope=BOOKING_SCOPE,
                actor_ref=str(request.user.pk),
                payload=payload,
                action=action,
            )
        except DomainError as error:
            messages.error(request, error.detail)
            return self.render_to_response(self.get_context_data(form=form))
        code = (outcome.body or {}).get("code")
        messages.success(request, "رزرو شما ثبت شد؛ کد پیگیری را نزد خود نگه دارید.")
        return redirect("web:reservation_detail", code=code)


class QuotePartialView(ProfileRequiredMixin, View):
    """برآورد زندهٔ رزرو (HTMX) — همهٔ قیدها پیش از ثبت نهایی نشان داده می‌شود."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """ساخت «برآورد» و رندر قطعهٔ HTML با نتیجه یا دلایل رد."""
        form = BookingForm(request.POST, initial={"idempotency_key": uuid.uuid4().hex})
        if not form.is_valid():
            return render(
                request,
                "web/partials/quote.html",
                {"form": form, "max_cells": policy_int("reservation.max_cells")},
            )
        unit = form.unit
        if unit is None:  # اعتبارسنجی فرم تضمین کرده است؛ اینجا فقط باریک‌سازی نوع
            raise Http404("واحد مورد نظر یافت نشد.")
        try:
            quote = reservation_services.build_quote(
                unit=unit,
                start=form.cleaned_data["start"],
                end=form.cleaned_data["end"],
                guests_count=int(form.cleaned_data["guests_count"]),
                guest=current_user(request),
            )
        except DomainError as error:
            return render(
                request,
                "web/partials/quote.html",
                {"domain_error": error.detail, "max_cells": policy_int("reservation.max_cells")},
            )
        return render(
            request,
            "web/partials/quote.html",
            {
                "quote": quote,
                "unit": unit,
                "alternatives": quote.alternatives[:MAX_QUOTE_ALTERNATIVES],
                "max_cells": policy_int("reservation.max_cells"),
            },
        )


class MyReservationsView(DashboardLoginMixin, TemplateView):
    """«رزروهای من» — اقامت‌های جاری/آینده و سابقهٔ گذشته."""

    template_name = "web/my_reservations.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """گروه‌بندی رزروهای کاربر به آینده و گذشته."""
        context = super().get_context_data(**kwargs)
        user = current_user(self.request)
        context.update(
            {
                "upcoming": list(reservation_selectors.reservations_for_user(user)),
                "past": list(reservation_selectors.reservations_for_user(user, scope="past")),
                "can_book": user.is_profile_complete,
            }
        )
        return context


class ReservationDetailView(DashboardLoginMixin, TemplateView):
    """جزئیات یک رزرو — کد پیگیری، بازه، وضعیت و امکان لغو."""

    template_name = "web/reservation_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """یافتن رزرو با کد؛ فقط صاحب رزرو یا کارکنان (در غیر این صورت ۴۰۴)."""
        context = super().get_context_data(**kwargs)
        reservation = self._visible_reservation()
        context.update(
            {
                "reservation": reservation,
                "cancel_form": CancelForm(initial={"expected_version": reservation.version}),
                "is_staff": current_user(self.request).is_staff_member,
            }
        )
        return context

    def _visible_reservation(self) -> Any:
        """رزرو قابل مشاهده برای کاربر جاری؛ ۴۰۴ برای دیگران."""
        reservation = reservation_selectors.reservation_by_code(str(self.kwargs["code"]))
        user = current_user(self.request)
        if reservation is None or (reservation.guest_id != user.pk and not user.is_staff_member):
            raise Http404("رزروی با این کد برای شما وجود ندارد.")
        return reservation


class ReservationCertificateView(ReservationDetailView):
    """گواهی اقامت — برگهٔ چاپی با QR تأیید و کد کوتاه آفلاین.

    دو مسیر تأیید روی خود کاغذ چاپ می‌شود: QR (آنلاین) و کد کوتاه ۸ نویسه‌ای
    (آفلاین). نشانی پایه از خودِ درخواست گرفته می‌شود تا گواهی روی هر دامنه‌ای
    که سایت بالا آمده درست کار کند — نه فقط دامنهٔ تنظیمات.
    """

    template_name = "web/certificate.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ گواهی: محتوای امضاشده، QR درون‌خطی و نشانی بازرسی آفلاین."""
        context = super().get_context_data(**kwargs)
        reservation = context["reservation"]
        base_url = self.request.build_absolute_uri("/")
        certificate = build_certificate(reservation)
        context.update(
            {
                "certificate": certificate,
                "fields": certificate.fields(),
                "qr_svg": certificate_qr_svg(certificate, base_url=base_url),
                "verify_url": certificate_verify_url(certificate, base_url=base_url),
                "offline_url": self.request.build_absolute_uri(reverse("web:certificate-offline")),
                "issued_at": timezone.now(),
            }
        )
        return context


class CancelReservationView(DashboardLoginMixin, View):
    """لغو رزرو توسط زائر — سانس بلافاصله آزاد می‌شود."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """لغو با بررسی مالکیت و نسخهٔ خوش‌بینانه (ضد لغو تصادفی)."""
        reservation = reservation_selectors.reservation_by_code(str(kwargs["code"]))
        if reservation is None or reservation.guest_id != current_user(request).pk:
            raise Http404("رزروی با این کد برای شما وجود ندارد.")
        form = CancelForm(request.POST)
        if not form.is_valid():
            messages.error(request, "درخواست لغو معتبر نبود؛ دوباره تلاش کنید.")
            return redirect("web:reservation_detail", code=reservation.code)
        try:
            reservation_services.cancel_reservation(
                reservation=reservation,
                by=current_user(request),
                reason_kind=str(form.cleaned_data["reason_kind"]),
                reason=str(form.cleaned_data.get("reason") or ""),
                is_staff=current_user(request).is_staff_member,
                request=request,
            )
        except DomainError as error:
            messages.error(request, error.detail)
            return redirect("web:reservation_detail", code=reservation.code)
        messages.success(request, "رزرو شما لغو شد و سانس برای دیگر زائران آزاد گردید.")
        return redirect("web:my_reservations")
