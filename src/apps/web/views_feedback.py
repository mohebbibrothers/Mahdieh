"""نماهای نظرسنجی پس از اقامت — «نظر زائر، بدون دردسر».

دو نما، دو مخاطب:
    • ``FeedbackView``: صفحهٔ عمومی توکن‌محور برای زائر. بدون ورود، بدون نشست؛
      توکن امضاشدهٔ روی پیامک همان مجوز است. یک پاسخ برای هر رزرو.
    • ``FeedbackRequestView``: دکمهٔ کارکنان در تابلوی پذیرش برای فرستادن پیامک
      نظرسنجی (با امکان یادآوری دوباره).

قاعدهٔ طراحی: زائر باید در کمتر از ۲۰ ثانیه بتواند نظر بدهد؛ پس فرم فقط یک
گام دارد، امتیاز کلی اجباری است و بقیه اختیاری — با این حال همهٔ پاسخ‌ها به
یک رزرو واقعی گره خورده‌اند، پس داده برای تحلیل قابل‌اعتماد است.
"""

from __future__ import annotations

from typing import Any, Final

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import View

from apps.core.errors import DomainError
from apps.reservation import feedback as feedback_services
from apps.reservation.feedback import FeedbackError
from apps.reservation.models import GuestFeedback
from apps.web.mixins import StaffRequiredMixin, current_user
from apps.web.views_staff import reservation_for_staff

RATING_SCALE: Final[tuple[int, ...]] = (1, 2, 3, 4, 5)
RATING_LABELS: Final[dict[int, str]] = {
    1: "ضعیف",
    2: "قابل‌بهبود",
    3: "متوسط",
    4: "خوب",
    5: "عالی",
}
SUBSCORE_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("cleanliness", "پاکیزگی"),
    ("hospitality", "برخورد و پذیرش"),
    ("facilities", "امکانات"),
    ("comfort", "آسایش و سکوت"),
)
COMMENT_PLACEHOLDER: Final[str] = "چه چیزی خوب بود و چه چیزی را می‌شد بهتر کرد؟"
NOT_FOUND_STATUS: Final[int] = 404


def _subscores(raw: dict[str, Any]) -> dict[str, int | None]:
    """تبدیل ورودی خام فرم به نگاشت زیرامتیازها (خالی ⇒ ``None``)."""
    scores: dict[str, int | None] = {}
    for field, _label in SUBSCORE_LABELS:
        value = str(raw.get(field) or "").strip()
        scores[field] = int(value) if value.isdigit() else None
    return scores


class FeedbackView(View):
    """صفحهٔ نظرسنجی توکن‌محور — نمایش فرم، سپس ثبت و صفحهٔ سپاسگزاری."""

    template_name = "web/feedback.html"

    def get(self, request: HttpRequest, token: str, *args: Any, **kwargs: Any) -> HttpResponse:
        """نمایش فرم برای توکن معتبر؛ توکن نامعتبر پاسخ ۴۱۰ می‌گیرد."""
        return self._render(request, token)

    def post(self, request: HttpRequest, token: str, *args: Any, **kwargs: Any) -> HttpResponse:
        """ثبت پاسخ زائر با اعتبارسنجی کامل سرویس دامنه."""
        rating = str(request.POST.get("rating") or "").strip()
        if not rating.isdigit():
            return self._render(
                request, token, error="لطفاً امتیاز کلی اقامت را انتخاب کنید.", status=400
            )
        recommend_raw = str(request.POST.get("would_recommend") or "").strip()
        recommend = None if recommend_raw == "" else recommend_raw == "yes"
        try:
            feedback = feedback_services.submit_feedback(
                token=token,
                rating=int(rating),
                comment=str(request.POST.get("comment") or ""),
                would_recommend=recommend,
                scores=_subscores(request.POST),
                request=request,
            )
        except DomainError as error:
            return self._render(request, token, error=error.detail, status=409)
        return render(
            request,
            self.template_name,
            {
                "submitted": True,
                "feedback": feedback,
                "thanks": True,
                "reservation": feedback.reservation,
            },
        )

    def _render(
        self,
        request: HttpRequest,
        token: str,
        *,
        error: str = "",
        status: int = 200,
    ) -> HttpResponse:
        """رندر فرم با وضعیت رزرو؛ خطای توکن به صفحهٔ ۴۱۰ خوانا تبدیل می‌شود."""
        try:
            reservation = feedback_services.resolve_token(token)
        except DomainError as domain_error:
            return render(
                request,
                self.template_name,
                {"invalid": True, "error": domain_error.detail},
                status=NOT_FOUND_STATUS if domain_error.status == NOT_FOUND_STATUS else 410,
            )
        existing = GuestFeedback.objects.filter(reservation=reservation).first()
        if existing is not None and existing.is_submitted:
            # وضعیت HTTP همان است که فراخواننده خواسته: بازدید دوبارهٔ صفحه ۲۰۰،
            # تلاش برای پاسخ دوم ۴۰۹ — صفحهٔ «سپاس» در هر دو حالت نشان داده می‌شود.
            return render(
                request,
                self.template_name,
                {"submitted": True, "feedback": existing, "reservation": reservation},
                status=status,
            )
        return render(
            request,
            self.template_name,
            {
                "reservation": reservation,
                "token": token,
                "ratings": RATING_SCALE,
                "rating_labels": RATING_LABELS,
                "subscores": SUBSCORE_LABELS,
                "comment_placeholder": COMMENT_PLACEHOLDER,
                "error": error,
            },
            status=status,
        )


class FeedbackRequestView(StaffRequiredMixin, View):
    """ارسال (یا یادآوری) پیامک نظرسنجی برای یک اقامت پایان‌یافته."""

    def post(self, request: HttpRequest, code: str, *args: Any, **kwargs: Any) -> HttpResponse:
        """اجرای درخواست نظرسنجی و بازگشت به همان صفحهٔ تابلوی پذیرش."""
        reservation = reservation_for_staff(code)
        resend = str(request.POST.get("resend") or "") == "1"
        try:
            feedback_services.request_feedback(
                reservation=reservation,
                by=current_user(request),
                request=request,
                resend=resend,
            )
        except FeedbackError as error:
            messages.warning(request, error.detail)
        except DomainError as error:
            messages.error(request, error.detail)
        else:
            messages.success(
                request,
                f"پیوند نظرسنجی برای {reservation.code} پیامک شد."
                if not resend
                else f"یادآوری نظرسنجی برای {reservation.code} فرستاده شد.",
            )
        day = str(request.POST.get("board_day") or "").strip()
        board = reverse("web:board")
        return redirect(f"{board}?day={day}" if day else board)
