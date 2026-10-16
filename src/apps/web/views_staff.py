"""نماهای کارکنان — تابلوی پذیرش، کنش‌های عملیاتی و مدیریت بلوک‌ها.

همهٔ نماهای این فایل پشت ``StaffRequiredMixin`` هستند و هر کنش، رخداد و حسابرسی
خود را از سرویس دامنه می‌گیرد؛ این لایه هیچ قاعده‌ای را بازنویسی نمی‌کند.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.core.errors import DomainError
from apps.inventory import selectors as inventory_selectors, services as inventory_services
from apps.inventory.models import Block
from apps.reservation import (
    feedback as feedback_services,
    selectors as reservation_selectors,
    services as reservation_services,
)
from apps.reservation.availability import availability_matrix
from apps.reservation.policy import policy_snapshot
from apps.web import presenters
from apps.web.forms import BlockForm, ExportForm, MoveForm, StaffCancelForm
from apps.web.mixins import StaffRequiredMixin, current_user
from apps.web.views import parse_day

BOARD_LOOKAHEAD_DAYS: Final[int] = 7
BOARD_POLL_SECONDS: Final[int] = 30
TRANSITION_ACTIONS: Final[frozenset[str]] = frozenset({"check_in", "check_out", "no_show"})


def reservation_for_staff(code: str) -> Any:
    """یافتن رزرو برای کنش کارکنان؛ ۴۰۴ در صورت نبود."""
    reservation = reservation_selectors.reservation_by_code(code)
    if reservation is None:
        raise Http404("رزروی با این کد یافت نشد.")
    return reservation


class BoardContextMixin:
    """زمینهٔ مشترک تابلوی کارکنان — صفحهٔ کامل و قطعهٔ به‌روزرسانی."""

    request: HttpRequest

    def board_context(self) -> dict[str, Any]:
        """زمینهٔ تابلو برای یک روز: ورودی‌ها، در اقامت، بلوک‌ها و شاخص‌ها."""
        day = parse_day(self.request.GET.get("day"))
        window_start, window_end = inventory_selectors.day_window(day)
        lookahead_end = window_start + timedelta(days=BOARD_LOOKAHEAD_DAYS)
        rows = list(reservation_selectors.staff_board(start=window_start, end=window_end))
        upcoming = list(reservation_selectors.staff_board(start=window_end, end=lookahead_end))
        grouped = presenters.group_board(rows, day=day)
        units = list(inventory_selectors.active_units())
        matrix = availability_matrix(start_day=day, days=1, staff=True, use_cache=True)
        return {
            "day": day,
            "prev_day": (day - timedelta(days=1)).isoformat(),
            "next_day": (day + timedelta(days=1)).isoformat(),
            "rows": rows,
            "grouped": grouped,
            "upcoming": upcoming,
            "kpis": presenters.board_kpis(grouped, units_total=len(units)),
            "blocks": list(
                inventory_selectors.blocks_overlapping(start=window_start, end=window_end)
            ),
            "units": units,
            "matrix": matrix,
            "stats": presenters.matrix_stats(matrix),
            "block_form": BlockForm(),
            "export_form": ExportForm(),
            "poll_seconds": BOARD_POLL_SECONDS,
            "policies": policy_snapshot(),
            "feedback_states": feedback_services.board_states([*rows, *upcoming]),
            "now": timezone.now(),
        }


class BoardView(StaffRequiredMixin, BoardContextMixin, TemplateView):
    """تابلوی پذیرش — تصویر کامل امروز زائرسرا در یک صفحه."""

    template_name = "web/board.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ کامل تابلو."""
        context = super().get_context_data(**kwargs)
        context.update(self.board_context())
        return context


class BoardPartialView(StaffRequiredMixin, BoardContextMixin, TemplateView):
    """قطعهٔ HTMX تابلوی کارکنان — به‌روزرسانی دوره‌ای بدون بارگذاری صفحه."""

    template_name = "web/partials/board_table.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """همان دادهٔ تابلو، بدون پوسته و بدون فرم‌های سنگین."""
        context = super().get_context_data(**kwargs)
        context.update(self.board_context())
        context["partial"] = True
        return context


class BoardActionView(StaffRequiredMixin, View):
    """کنش‌های عملیاتی روی یک رزرو: پذیرش، تخلیه، عدم‌حضور، لغو و جابه‌جایی."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """اجرای کنش درخواستی از طریق سرویس دامنه و بازگشت به تابلو."""
        action = str(kwargs["action"])
        reservation = reservation_for_staff(str(kwargs["code"]))
        handler = {
            "check_in": self._check_in,
            "check_out": self._check_out,
            "no_show": self._no_show,
            "cancel": self._cancel,
            "move": self._move,
        }.get(action)
        if handler is None:
            raise Http404("کنش درخواستی پشتیبانی نمی‌شود.")
        try:
            handler(request, reservation)
        except DomainError as error:
            messages.error(request, error.detail)
        return self._back(request)

    def _check_in(self, request: HttpRequest, reservation: Any) -> None:
        """پذیرش مهمان."""
        reservation_services.check_in(
            reservation=reservation, by=current_user(request), request=request
        )
        messages.success(request, f"مهمان {reservation.code} پذیرش شد.")

    def _check_out(self, request: HttpRequest, reservation: Any) -> None:
        """تخلیهٔ واحد."""
        reservation_services.check_out(
            reservation=reservation, by=current_user(request), request=request
        )
        messages.success(request, f"رزرو {reservation.code} تخلیه شد.")

    def _no_show(self, request: HttpRequest, reservation: Any) -> None:
        """ثبت عدم‌حضور با دلیل اختیاری."""
        reservation_services.mark_no_show(
            reservation=reservation,
            by=current_user(request),
            reason=str(request.POST.get("reason") or ""),
            request=request,
        )
        messages.success(request, f"عدم‌حضور برای {reservation.code} ثبت شد.")

    def _cancel(self, request: HttpRequest, reservation: Any) -> None:
        """لغو کارکنان — دلیل اجباری."""
        form = StaffCancelForm(request.POST)
        if not form.is_valid():
            messages.error(request, "برای لغو، انتخاب دسته و نوشتن دلیل الزامی است.")
            return
        reservation_services.cancel_reservation(
            reservation=reservation,
            by=current_user(request),
            reason_kind=str(form.cleaned_data["reason_kind"]),
            reason=str(form.cleaned_data["reason"]),
            is_staff=True,
            request=request,
        )
        messages.success(request, f"رزرو {reservation.code} لغو و سانس آزاد شد.")

    def _move(self, request: HttpRequest, reservation: Any) -> None:
        """جابه‌جایی رزرو به واحد/زمان دیگر با دلیل."""
        form = MoveForm(request.POST)
        if not form.is_valid():
            messages.error(request, "اطلاعات جابه‌جایی کامل نیست؛ واحد، تاریخ و دلیل لازم است.")
            return
        target_unit = inventory_selectors.unit_by_code(str(form.cleaned_data["unit_code"]))
        if target_unit is None:
            raise Http404("واحد مقصد یافت نشد.")
        reservation_services.move_reservation(
            reservation=reservation,
            unit=target_unit,
            start=form.cleaned_data["start"],
            end=form.cleaned_data["end"],
            by=current_user(request),
            reason=str(form.cleaned_data["reason"]),
            expected_version=int(form.cleaned_data["expected_version"]),
            request=request,
        )
        messages.success(request, f"رزرو {reservation.code} جابه‌جا شد.")

    def _back(self, request: HttpRequest) -> HttpResponse:
        """بازگشت به تابلو با حفظ روز انتخاب‌شده."""
        day = request.POST.get("board_day")
        url = reverse("web:board")
        return redirect(f"{url}?day={day}" if day else url)


class BlockCreateView(StaffRequiredMixin, View):
    """ساخت بلوک نگهداری/نظافت — سراسری یا برای یک واحد."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """اعتبارسنجی فرم و ساخت بلوک از طریق سرویس موجودی."""
        form = BlockForm(request.POST)
        if not form.is_valid():
            problems = [str(item) for item in form.errors.get("__all__", [])]
            detail = "؛ ".join(problems) if problems else "اطلاعات فرم بلوک کامل نیست."
            messages.error(request, "بلوک ساخته نشد: " + detail)
            return _board_redirect(request)
        unit = (
            inventory_selectors.unit_by_code(str(form.cleaned_data["unit_code"]))
            if form.cleaned_data["unit_code"]
            else None
        )
        try:
            inventory_services.create_block(
                start=form.cleaned_data["start"],
                end=form.cleaned_data["end"],
                kind=str(form.cleaned_data["kind"]),
                reason=str(form.cleaned_data["reason"]),
                unit=unit,
                actor=current_user(request),
                request=request,
            )
        except DomainError as error:
            messages.error(request, error.detail)
        else:
            messages.success(request, "بلوک با موفقیت ثبت شد؛ سانس‌های آن آزاد نیستند.")
        return _board_redirect(request)


class BlockRemoveView(StaffRequiredMixin, View):
    """برداشتن یک بلوک فعال — با دلیل اجباری."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """یافتن بلوک و برداشتن آن از طریق سرویس."""
        block = Block.objects.filter(pk=kwargs["block_id"]).first()
        if block is None:
            raise Http404("بلوکی با این شناسه یافت نشد.")
        reason = str(request.POST.get("reason") or "").strip()
        try:
            inventory_services.remove_block(
                block=block, actor=current_user(request), reason=reason, request=request
            )
        except DomainError as error:
            messages.error(request, error.detail)
        else:
            messages.success(request, "بلوک برداشته شد و سانس‌ها به تقویم بازگشتند.")
        return _board_redirect(request)


def _board_redirect(request: HttpRequest) -> HttpResponse:
    """بازگشت به تابلو با روز درخواستی (فیلد ``board_day`` عمدی است تا با
    فیلد تاریخِ فرم‌های جابه‌جایی/بلوک تداخل نکند)."""
    day = request.POST.get("board_day")
    url = reverse("web:board")
    return redirect(f"{url}?day={day}" if day else url)


def board_day_options(start: date, *, count: int) -> list[date]:
    """فهرست روزهای پیش‌رو برای انتخاب سریع در تابلو (کمک‌کنندهٔ قالب)."""
    return [start + timedelta(days=offset) for offset in range(count)]
