"""نماهای تحلیلی و حاکمیتی کارکنان — داشبورد، صف انتظار و پنل سیاست.

تفکیک فایل‌ها عمدی است:
    • ``views.py``        → مسیر زائر (عمومی و خصوصی)
    • ``views_staff.py``  → عملیات روزانهٔ پذیرش
    • ``views_insights.py`` → بینش و حاکمیت (تحلیل، صف، سیاست، تأیید گواهی)

هر نما فقط **می‌خواند** و از سرویس‌های دامنه استفاده می‌کند؛ هیچ نوشتن مستقیم
روی مدل انجام نمی‌شود (قاعدهٔ لایه‌بندی که تست معماری هم آن را نگهبانی می‌کند).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.analytics import charts, insights
from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.errors import DomainError
from apps.identity import mfa
from apps.identity.permissions import authenticated_user
from apps.inventory.selectors import active_units
from apps.reservation.certificate import verify_online
from apps.reservation.policy import REASON_MIN_LENGTH, all_policies, set_policy
from apps.waitlist import selectors as waitlist_selectors, services as waitlist_services
from apps.web.mixins import StaffRequiredMixin
from apps.web.presenters import fa

WINDOW_CHOICES = (7, 30, 90)


class InsightsView(StaffRequiredMixin, TemplateView):
    """داشبورد تحلیلی — «نبض زائرسرا» در یک نگاه."""

    template_name = "web/board_insights.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """تحلیل‌ها + نمودارهای SVG ساخته‌شده در سرور."""
        context = super().get_context_data(**kwargs)
        raw_days = str(self.request.GET.get("days", "30"))
        days = int(raw_days) if raw_days.isdigit() and int(raw_days) in WINDOW_CHOICES else 30
        data = insights.dashboard(days=days)

        series = data["points"][-60:]
        occupancy_chart = charts.line_chart(
            [(point.date_jalali.split()[0], point.rate) for point in series],
            label="روند نرخ اشغال",
        )
        trend_values = [point.rate for point in series]
        trend_spark = charts.sparkline(trend_values, label="خلاصهٔ روند اشغال")

        planning = data["planning"]
        planned_days = len(planning["labels"])
        heat = (
            charts.heatmap(
                rows=[(row["code"], list(row["rates"])) for row in planning["rows"]],
                columns=planned_days,
                column_labels=list(planning["labels"]),
                label=f"بار پیش‌روی {fa(planned_days)} روز — اشغال هر واحد",
            )
            if planned_days and planning["rows"]
            else ""
        )

        utilization_bars = charts.bars(
            [
                (f"{row['code']} · طبقهٔ {fa(row['floor'])}", row["occupancy"])
                for row in data["unit_utilization"]
            ],
            label="نرخ اشغال واحدها",
            accent=charts.PALETTE["teal"],
        )
        donut = charts.donut(
            [
                ("پذیرش‌شده", data["guest_quality"]["completion_rate"], charts.PALETTE["teal"]),
                ("لغو‌شده", data["guest_quality"]["cancel_rate"], charts.PALETTE["gold"]),
                ("عدم‌حضور", data["guest_quality"]["no_show_rate"], charts.PALETTE["danger"]),
            ],
            label="ترکیب سرنوشت رزروها",
        )
        gauge = charts.gauge(data["occupancy"], label="نرخ اشغال بازه")
        satisfaction_bars = charts.bars(
            [
                (row["label"], row["rate"])
                for row in data["satisfaction"]["breakdown"]
                if row["average"] is not None
            ],
            label="میانگین زیرشاخص‌های رضایت",
            accent=charts.PALETTE["gold"],
        )

        context.update(
            {
                "days": days,
                "window_choices": WINDOW_CHOICES,
                "data": data,
                "series": series,
                "chart_occupancy": occupancy_chart,
                "chart_spark": trend_spark,
                "chart_heat": heat,
                "planning": planning,
                "chart_units": utilization_bars,
                "chart_donut": donut,
                "chart_gauge": gauge,
                "chart_satisfaction": satisfaction_bars,
                "today": timezone.localdate(),
            }
        )
        return context


class WaitlistBoardView(StaffRequiredMixin, TemplateView):
    """تابلوی صف انتظار — نوبت‌ها، پیشنهادهای باز و کنش‌های کارکنان."""

    template_name = "web/board_waitlist.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """صف، پیشنهادها، آمار و مهلت‌های نزدیک."""
        context = super().get_context_data(**kwargs)
        overview = waitlist_selectors.waitlist_overview()
        context.update(
            {
                "queue": waitlist_selectors.queue_page(statuses=None, limit=100),
                "offers": waitlist_selectors.open_offers(limit=50),
                "stats": waitlist_services.queue_stats(),
                "overview": overview,
                "now": timezone.now(),
            }
        )
        return context


class WaitlistWithdrawStaffView(StaffRequiredMixin, View):
    """انصراف کارکنان از یک درخواست صف (با دلیل اجباری)."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, entry_id: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        """بستن درخواست و بازگشت به تابلو با پیام."""
        entry = waitlist_selectors.entry_by_id(entry_id)
        if entry is None:
            raise Http404("درخواست صف انتظار یافت نشد.")
        reason = str(request.POST.get("reason", "")).strip()
        try:
            waitlist_services.withdraw_entry(
                entry=entry,
                by=authenticated_user(request),
                reason=reason,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, exc.detail)
        else:
            messages.success(request, f"درخواست {entry.guest.display_name} از صف بسته شد.")
        return redirect("web:board-waitlist")


class PolicyBoardView(StaffRequiredMixin, TemplateView):
    """پنل سیاست‌ها — خواندن برای همهٔ کارکنان، تغییر برای مدیر."""

    template_name = "web/board_policies.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """همهٔ سیاست‌ها با برچسب، مقدار مؤثر، پیش‌فرض و تاریخچهٔ تغییر."""
        from apps.reservation.models import PolicyRevision

        context = super().get_context_data(**kwargs)
        policies = all_policies()
        history = PolicyRevision.objects.select_related("changed_by", "policy").order_by(
            "-created_at"
        )[:12]
        context.update(
            {
                "policies": policies,
                "changed_policies": [item for item in policies if item.changed],
                "defaults_count": sum(1 for item in policies if not item.changed),
                "history": history,
                "can_edit": bool(getattr(authenticated_user(self.request), "is_manager", False)),
                "reason_min": REASON_MIN_LENGTH,
                "units": list(active_units()),
            }
        )
        return context


class PolicyUpdateView(StaffRequiredMixin, View):
    """تغییر یک سیاست — فقط مدیر، فقط با دلیل، با حسابرسی و نسخهٔ تازه."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """اعمال تغییر و بازگشت به پنل سیاست‌ها."""
        user = authenticated_user(request)
        if not user.is_manager:
            messages.error(request, "تغییر سیاست‌ها فقط در اختیار مدیر است.")
            return redirect("web:board-policies")
        if not mfa.is_fresh(request):
            messages.error(
                request, "تغییر سیاست کار حساسی است؛ ابتدا کد اپ احراز هویت را وارد کنید."
            )
            return redirect(f"{reverse('web:mfa')}?next={reverse('web:board-policies')}")

        key = str(request.POST.get("key", "")).strip()
        raw_value = request.POST.get("value", "")
        reason = str(request.POST.get("reason", "")).strip()
        if len(reason) < REASON_MIN_LENGTH:
            messages.error(request, f"ثبت دلیل اجباری است (حداقل {fa(REASON_MIN_LENGTH)} نویسه).")
            return redirect("web:board-policies")
        try:
            policy = set_policy(
                key=key, value=raw_value, actor=user, reason=reason, request=request
            )
        except DomainError as exc:
            messages.error(request, exc.detail)
        else:
            messages.success(
                request,
                f"سیاست «{policy.key}» به نسخهٔ {fa(policy.version)} به‌روز شد و بی‌درنگ اثر می‌کند.",
            )
        return redirect("web:board-policies")


class CertificateVerifyView(View):
    """تأیید آنلاین گواهی اقامت از روی توکن امضاشدهٔ QR — بدون نیاز به ورود.

    نکتهٔ امنیتی: محتوای نمایش‌داده‌شده **فقط** از توکن امضاشده می‌آید؛ حتی اگر
    رکورد دیتابیس پیدا شود، تناقض آن نمایش داده می‌شود تا جعل برملا گردد.
    """

    template_name = "web/verify.html"

    def get(self, request: HttpRequest, token: str, *args: Any, **kwargs: Any) -> HttpResponse:
        """نمایش نتیجهٔ تأیید (معتبر / نامعتبر) با جزئیات."""
        from django.shortcuts import render

        result = verify_online(token)
        payload, valid, mismatch = result.payload, result.crypto_valid, result.mismatch
        error, reservation = result.error, result.reservation

        audit_record(
            AuditEntry(
                action=AuditAction.SENSITIVE_DATA_READ,
                actor_label="تأیید گواهی",
                target_type="reservation",
                target_id=str(payload.get("code", "")),
                outcome="success" if (valid and not mismatch) else "denied",
                reason="تأیید آنلاین گواهی اقامت",
                metadata={"valid": valid, "mismatch": mismatch},
                request=request,
            )
        )
        return render(
            request,
            self.template_name,
            {
                "valid": valid and not mismatch,
                "crypto_valid": valid,
                "mismatch": mismatch,
                "error": error,
                "payload": payload,
                "reservation": reservation,
                "checked_at": timezone.now(),
            },
            status=200 if (valid and not mismatch) else 404,
        )


class CertificateOfflineCheckView(View):
    """بازرسی آفلاین — بازمحاسبهٔ کد کوتاه از روی فیلدهای گواهی چاپی.

    این صفحه **به اینترنت و دیتابیس نیاز ندارد**؛ کافی است کسی فیلدهای روی
    کاغذ را وارد کند. برای همین روی خود گواهی هم نشانی‌اش چاپ می‌شود.
    """

    template_name = "web/verify.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """نمایش فرم بازرسی آفلاین."""
        from django.shortcuts import render

        return render(request, self.template_name, {"offline_form": True})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """بازمحاسبهٔ کد کوتاه و اعلام نتیجه."""
        from django.shortcuts import render

        from apps.reservation.certificate import verify_short_code

        payload = {
            "code": str(request.POST.get("code", "")).strip().upper(),
            "holder": str(request.POST.get("holder", "")).strip(),
            "unit": str(request.POST.get("unit", "")).strip(),
            "floor": _as_int(request.POST.get("floor")),
            "guests": _as_int(request.POST.get("guests")),
            "starts": str(request.POST.get("starts", "")).strip(),
            "ends": str(request.POST.get("ends", "")).strip(),
        }
        short_code = str(request.POST.get("short_code", "")).strip()
        valid = verify_short_code(payload, short_code)
        audit_record(
            AuditEntry(
                action=AuditAction.SENSITIVE_DATA_READ,
                actor_label="بازرسی آفلاین گواهی",
                target_type="reservation",
                target_id=str(payload["code"]),
                outcome="success" if valid else "denied",
                reason="بازرسی آفلاین کد کوتاه گواهی",
                metadata={"valid": valid},
                request=request,
            )
        )
        return render(
            request,
            self.template_name,
            {
                "offline_form": True,
                "offline_result": True,
                "valid": valid,
                "payload": payload,
                "checked_at": timezone.now(),
            },
            status=200,
        )


def _as_int(value: Any) -> int:
    """تبدیل امن ورودی فرم به عدد (برای جلوگیری از ۵۰۰ در فرم دستی)."""
    text = "".join(char for char in str(value or "") if char.isdigit())
    return int(text) if text else 0


def date_window(days: int) -> tuple[date, date]:
    """بازهٔ تاریخ برای نماهای تحلیلی."""
    today = timezone.localdate()
    return today - timedelta(days=days), today


def insights_url(*, days: int) -> str:
    """نشانی داشبورد تحلیلی با پنجرهٔ دلخواه."""
    return f"{reverse('web:board-insights')}?days={days}"
