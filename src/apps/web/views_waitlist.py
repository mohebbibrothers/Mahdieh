"""نماهای صف انتظار برای زائر — ثبت، مشاهده، پذیرش یک‌ضربه‌ای و انصراف.

طراحی تجربهٔ کاربری:
    • زائر از صفحهٔ تقویم/رزرو «درخواست انتظار» می‌دهد (بدون گم‌شدن در فرم).
    • لینک پیامک، **بدون ورود دوباره** به صفحهٔ پیشنهاد می‌رسد (توکن امضاشده)
      و با یک دکمه به رزرو تبدیل می‌شود؛ اگر مالک نباشد، برای ورود هدایت می‌شود.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.core.errors import DomainError
from apps.identity.permissions import authenticated_user
from apps.inventory.selectors import unit_by_code
from apps.reservation.policy import policy_int
from apps.waitlist import selectors, services
from apps.waitlist.tokens import OfferTokenError, make_token, offer_urls, read_token
from apps.web.mixins import DashboardLoginMixin, ProfileRequiredMixin
from apps.web.presenters import fa


class MyWaitlistView(ProfileRequiredMixin, TemplateView):
    """«درخواست‌های انتظار من» — نوبت، وضعیت و تاریخچهٔ پیشنهادها."""

    template_name = "web/my_waitlist.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """درخواست فعال، تاریخچه و پیشنهاد باز (اگر باشد)."""
        context = super().get_context_data(**kwargs)
        user = authenticated_user(self.request)
        active = selectors.active_entry_for(user)
        context.update(
            {
                "active": active,
                "position": services.queue_position(active) if active else 0,
                "history": selectors.entries_for_user(user, scope="all").exclude(pk=active.pk)
                if active
                else selectors.entries_for_user(user, scope="all"),
                "open_offer": _open_offer_for(active),
                "max_offers": policy_int("waitlist.max_offers_before_requeue"),
            }
        )
        return context


class JoinWaitlistView(ProfileRequiredMixin, View):
    """ثبت درخواست انتظار از مسیر وب (فرم صفحهٔ تقویم/واحد)."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """اعتبارسنجی سبک ورودی، سپس واگذاری به سرویس دامنه."""
        unit_code = str(request.POST.get("unit_code", "")).strip()
        unit = unit_by_code(unit_code) if unit_code else None
        if unit_code and unit is None:
            raise Http404("واحد مورد نظر یافت نشد.")

        raw_start = str(request.POST.get("start", "")).strip()
        raw_end = str(request.POST.get("end", "")).strip()
        try:
            start = _parse_moment(raw_start)
            end = _parse_moment(raw_end)
        except ValueError:
            messages.error(request, "بازهٔ درخواست را درست وارد کنید.")
            return redirect(request.POST.get("next") or reverse("web:calendar"))

        guests_raw = "".join(
            char for char in str(request.POST.get("guests_count", "1")) if char.isdigit()
        )
        guests = max(1, int(guests_raw or "1"))
        try:
            entry = services.join_waitlist(
                guest=authenticated_user(request),
                unit=unit,
                start=start,
                end=end,
                guests_count=guests,
                note=str(request.POST.get("note", ""))[:200],
                source="web",
                request=request,
            )
        except DomainError as exc:
            messages.error(request, exc.detail)
            return redirect(request.POST.get("next") or reverse("web:calendar"))

        position = services.queue_position(entry)
        messages.success(
            request,
            f"درخواست انتظار شما ثبت شد؛ نوبت شما {fa(position)} است. "
            "به‌محض آزاد شدن سانس، پیامک می‌گیرید.",
        )
        return redirect("web:my_waitlist")


class OfferRespondView(DashboardLoginMixin, View):
    """صفحهٔ پیشنهاد با پذیرش یک‌ضربه‌ای (توکن امضاشده در پیامک)."""

    template_name = "web/waitlist_offer.html"

    def get(
        self, request: HttpRequest, token: str, action: str, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """نمایش پیشنهاد برای کنش درخواستی."""
        offer, error = self._resolve(token, action)
        if error:
            return render(request, self.template_name, {"error": error}, status=410)
        assert offer is not None
        if not _owns(request, offer):
            return redirect(f"{reverse('web:login')}?next={request.get_full_path()}")
        return render(
            request,
            self.template_name,
            {
                "offer": offer,
                "action": action,
                "entry": offer.entry,
                "position": services.queue_position(offer.entry),
                "decline_reasons": _decline_reasons(),
                "seconds_left": offer.seconds_left,
            },
        )

    def post(
        self, request: HttpRequest, token: str, action: str, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """اجرای کنش (پذیرش یا رد) با بررسی مالکیت و مهلت."""
        offer, error = self._resolve(token, action)
        if error:
            messages.error(request, error)
            return redirect("web:my_waitlist")
        assert offer is not None
        user = authenticated_user(request)
        if not _owns(request, offer):
            return redirect(f"{reverse('web:login')}?next={request.get_full_path()}")

        try:
            if action == "decline":
                services.decline_offer(
                    offer=offer,
                    by=user,
                    reason_kind=str(request.POST.get("reason_kind") or "time_not_suitable"),
                    request=request,
                )
                messages.success(
                    request,
                    "پیشنهاد رد شد. اگر سانس بماند، نفر بعدی صف پیشنهاد را می‌گیرد.",
                )
                return redirect("web:my_waitlist")
            _, reservation = services.accept_offer(offer=offer, by=user, request=request)
        except DomainError as exc:
            messages.error(request, exc.detail)
            return redirect("web:my_waitlist")

        messages.success(request, f"سانس برای شما ثبت شد — کد رزرو {reservation.code}.")
        return redirect("web:reservation_detail", code=reservation.code)

    def _resolve(self, token: str, action: str) -> tuple[Any, str]:
        """اعتبارسنجی توکن و بارگذاری پیشنهاد؛ بازگشت ``(offer, error)``."""
        max_age = policy_int("waitlist.offer_window_minutes") * 60
        try:
            offer_id = read_token(token, expected_action=action, max_age_seconds=max_age)
        except OfferTokenError as exc:
            return None, str(exc)
        offer = selectors.offer_by_token_payload(offer_id)
        if offer is None:
            return None, "این پیشنهاد در سامانه پیدا نشد."
        return offer, ""


class WithdrawWaitlistView(ProfileRequiredMixin, View):
    """انصراف زائر از درخواست انتظار خودش."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, entry_id: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        """بستن درخواست و بازگشت به صفحهٔ «درخواست‌های من»."""
        entry = selectors.entry_by_id(entry_id)
        if entry is None:
            raise Http404("درخواست صف انتظار یافت نشد.")
        try:
            services.withdraw_entry(
                entry=entry,
                by=authenticated_user(request),
                reason=str(request.POST.get("reason", "")),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, exc.detail)
        else:
            messages.success(request, "درخواست انتظار شما بسته شد و نوبت آزاد گشت.")
        return redirect("web:my_waitlist")


def _open_offer_for(entry: Any) -> Any | None:
    """پیشنهاد بازِ یک درخواست (اگر داشته باشد) — یک کوئری، بدون پیمایش لیست."""
    if entry is None:
        return None
    return selectors.open_offer_for_entry(entry)


def offer_link(offer: Any, *, action: str, absolute: bool = False) -> str:
    """لینک امضاشدهٔ یک کنش روی پیشنهاد — یک منبع، دو کاربرد.

    پیامک به لینک **مطلق** نیاز دارد و قالب به لینک نسبی؛ هر دو از یک تابع
    ساخته می‌شوند تا اگر روزی شکل مسیر عوض شود، فقط یک جا تغییر کند.
    """
    if absolute:
        return offer_urls(offer.id)[action]
    return reverse(
        "web:waitlist-offer",
        kwargs={"token": make_token(offer_id=offer.id, action=action), "action": action},
    )


def _owns(request: HttpRequest, offer: Any) -> bool:
    """آیا کاربر جاری مالک پیشنهاد است (یا عضو کارکنان)؟"""
    user = request.user
    if not user.is_authenticated:
        return False
    if bool(getattr(user, "is_staff_member", False)):
        return True
    return bool(offer.entry.guest_id == user.id)


def _parse_moment(raw: str) -> Any:
    """تجزیهٔ زمان ISO؛ خطای مقدار نامعتبر با ``ValueError`` اعلام می‌شود."""
    text = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = timezone.make_aware(parsed)
    return parsed


def _decline_reasons() -> list[tuple[str, str]]:
    """دلایل رد پیشنهاد برای انتخاب زائر."""
    from apps.waitlist.choices import DeclineReason

    return [(str(value), str(label)) for value, label in DeclineReason.choices]
