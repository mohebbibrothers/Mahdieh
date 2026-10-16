"""خروجی تقویم — «اقامت من در تقویم خودم».

سه سطح دسترسی، سه خروجی، یک سازندهٔ مشترک:
    • زائر: تقویم یک رزرو (``/reservations/<code>/calendar.ics``) و تقویم همهٔ
      اقامت‌های خودش (``/my/calendar.ics``) — قابل اشتراک در گوشی.
    • کارکنان: تقویم یک بازه از کل زائرسرا (``/board/calendar.ics``) تا میز
      پذیرش بتواند همهٔ رزروها را در تقویم کاری خود ببیند (Outlook/Google).

نکتهٔ امنیتی: هر تماس، رکورد حسابرسی ``reservation.calendar_exported`` می‌گیرد؛
پروندهٔ تقویم دادهٔ شخصی دارد (نام واحد، کد رزرو) و نباید بی‌ردپا از سامانه بیرون
برود. پاسخ‌ها ``no-store`` هستند تا در کش واسط نمانند.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone
from django.views.generic import View

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record
from apps.reservation import ics, selectors as reservation_selectors
from apps.reservation.choices import ReservationStatus
from apps.web.mixins import DashboardLoginMixin, StaffRequiredMixin, current_user

STAFF_WINDOW_DAYS: Final[int] = 60
PRIVATE_CACHE: Final[str] = "private, no-store"


def ics_response(body: str, *, file_name: str) -> HttpResponse:
    """پاسخ HTTP آمادهٔ دانلود تقویم با سرصفحه‌های درست."""
    response = HttpResponse(body, content_type=ics.CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    response["Cache-Control"] = PRIVATE_CACHE
    response["X-Content-Type-Options"] = "nosniff"
    return response


class ReservationCalendarView(DashboardLoginMixin, View):
    """تقویم یک رزرو — از آنِ زائر، یا هر رزرو برای کارکنان."""

    def get(self, request: HttpRequest, code: str, *args: Any, **kwargs: Any) -> HttpResponse:
        """ساخت پروندهٔ تقویم برای کد رزرو — با کنترل مالکیت."""
        reservation = reservation_selectors.reservation_by_code(code)
        user = current_user(request)
        if reservation is None or (reservation.guest_id != user.pk and not user.is_staff_member):
            # پیام یکسان برای «وجود ندارد» و «مال شما نیست» — ضد افشای اطلاعات.
            raise Http404("رزروی با این کد برای شما یافت نشد.")
        record(
            AuditEntry(
                action=AuditAction.CALENDAR_EXPORTED,
                actor_id=user.pk,
                actor_label=user.display_name,
                target_type="reservation",
                target_id=str(reservation.pk),
                metadata={"code": reservation.code, "scope": "single"},
                request=request,
            )
        )
        return ics_response(
            ics.reservation_ics(reservation), file_name=ics.calendar_file_name(reservation.code)
        )


class MyCalendarView(DashboardLoginMixin, View):
    """تقویم همهٔ اقامت‌های زائر — اشتراک یک‌بارهٔ نشانی در گوشی."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """ساخت پروندهٔ تقویم از اقامت‌های جاری و پایان‌یافتهٔ همین زائر."""
        user = current_user(request)
        active = reservation_selectors.reservations_for_user(user).filter(
            status__in=ReservationStatus.active_statuses()
        )
        recent_cutoff = timezone.now() - timedelta(days=7)
        recent = reservation_selectors.reservations_for_user(user).filter(
            status=ReservationStatus.COMPLETED,
            stay_period__endswith__gte=recent_cutoff,
        )
        reservations = list(active) + list(recent)
        record(
            AuditEntry(
                action=AuditAction.CALENDAR_EXPORTED,
                actor_id=user.pk,
                actor_label=user.display_name,
                target_type="guest",
                target_id=str(user.pk),
                metadata={"scope": "guest", "count": len(reservations)},
                request=request,
            )
        )
        return ics_response(ics.ics_for(reservations), file_name="mahdieh-my-stays.ics")


class StaffCalendarView(StaffRequiredMixin, View):
    """تقویم کل زائرسرا برای میز پذیرش — بازهٔ پیش‌فرض ۶۰ روز."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """ساخت پروندهٔ تقویم از رزروهای فعال در بازهٔ درخواستی."""
        days = self._days(request)
        now = timezone.now()
        window_end = now + timedelta(days=days)
        reservations = list(
            reservation_selectors.reservations_overlapping(start=now, end=window_end)
            .filter(status__in=ReservationStatus.active_statuses())
            .select_related("unit", "guest")
            .order_by("stay_period")
        )
        user = current_user(request)
        record(
            AuditEntry(
                action=AuditAction.CALENDAR_EXPORTED,
                actor_id=user.pk,
                actor_label=user.display_name,
                target_type="board",
                target_id="",
                metadata={"scope": "staff", "count": len(reservations), "days": days},
                request=request,
            )
        )
        return ics_response(ics.ics_for(reservations), file_name=f"mahdieh-board-{days}d.ics")

    @staticmethod
    def _days(request: HttpRequest) -> int:
        """تفسیر پارامتر ``days`` با سقف ایمن — ورودی نامعتبر، پیش‌فرض می‌گیرد."""
        raw = str(request.GET.get("days") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return STAFF_WINDOW_DAYS
        return max(1, min(value, 365))
