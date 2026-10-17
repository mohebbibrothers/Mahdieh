"""نظرسنجی پس از اقامت — درخواست، توکن امضاشده و ثبت پاسخ.

چرا این ماژول «سرویس» است و نه یک فرم ساده:
    • **حساب‌دهی به یک رزرو واقعی:** هر نظرسنجی به یک رزرو پایان‌یافته گره
      خورده است؛ پس هیچ امتیاز بی‌پشتوانه‌ای ثبت نمی‌شود.
    • **بدون حساب کاربری:** زائر خستهٔ سفر نباید برای یک نظر، دوباره وارد شود.
      توکن امضاشده روی پیامک، همان مجوز است (بدون کوکی، بدون نشست).
    • **یک پاسخ برای هر اقامت:** تکرارپذیر نیست؛ هم کلید یکتای مدل و هم بررسی
      سرویس از آن مراقبت می‌کنند (دو لایه، چون داده‌های تحلیلی به آن وابسته‌اند).
    • **پنجرهٔ زمانی:** از پایان اقامت تا ۳۰ روز باز است؛ بعد از آن بسته می‌شود
      تا میانگین رضایت با خاطرات سال گذشته آلوده نشود.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, Final

from django.core import signing
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record
from apps.core.errors import ConflictError, NotFoundError, ValidationError
from apps.core.site import absolute_url
from apps.notifications.choices import SmsTemplateKey
from apps.notifications.services import send_sms
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import GuestFeedback, Reservation

logger = logging.getLogger(__name__)

SALT: Final[str] = "mahdieh.feedback"
TOKEN_MAX_AGE_DAYS: Final[int] = 45
WINDOW_DAYS: Final[int] = 30
COMMENT_MAX_LENGTH: Final[int] = 1200
MIN_RATING: Final[int] = 1
MAX_RATING: Final[int] = 5
# برچسب‌های فارسی مقیاس و زیرامتیازها — یگانه منبع برای صفحهٔ SSR و API.
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
SUBSCORE_FIELDS: Final[tuple[str, ...]] = tuple(field for field, _label in SUBSCORE_LABELS)


class FeedbackError(ValidationError):
    """نظرسنجی نامعتبر — لینک کهنه، رزرو نامناسب یا پاسخ تکراری."""


@dataclass(frozen=True, slots=True)
class FeedbackEligibility:
    """آیا این رزرو می‌تواند نظرسنجی بگیرد و اگر نه، چرا."""

    reservation_id: Any
    eligible: bool
    reason: str = ""

    def __bool__(self) -> bool:
        """پشتیبانی از ``if eligibility:`` در نماها."""
        return self.eligible


def issue_token(reservation: Reservation) -> str:
    """ساخت توکن امضاشدهٔ نظرسنجی برای یک رزرو (بدون ذخیره در دیتابیس)."""
    return signing.dumps(
        {"r": str(reservation.pk), "c": reservation.code}, salt=SALT, compress=True
    )


def resolve_token(token: str) -> Reservation:
    """خواندن توکن و بازگرداندن رزرو متناظر؛ در صورت نامعتبر بودن، خطای دامنه.

    خطای ``FeedbackError`` پیام یکسانی برای «توکن دست‌کاری‌شده» و «توکن منقضی»
    می‌دهد تا کسی با آزمون‌وخطا نتواند وجود رزروها را حدس بزند.
    """
    try:
        payload = signing.loads(token, salt=SALT, max_age=timedelta(days=TOKEN_MAX_AGE_DAYS))
    except signing.SignatureExpired as exc:
        raise FeedbackError(
            "این پیوند منقضی شده است؛ برای ارسال نظر با پذیرش تماس بگیرید."
        ) from exc
    except signing.BadSignature as exc:
        raise FeedbackError("این پیوند معتبر نیست.") from exc

    reservation = Reservation.objects.filter(pk=str(payload.get("r", ""))).first()
    if reservation is None:
        raise NotFoundError("رزروی برای این نظرسنجی یافت نشد.")
    return reservation


def feedback_url(reservation: Reservation, *, base_url: str | None = None) -> str:
    """نشانی صفحهٔ نظرسنجی برای این رزرو."""
    return absolute_url(f"/feedback/{issue_token(reservation)}/", base_url=base_url)


def eligibility(reservation: Reservation) -> FeedbackEligibility:
    """شرایط پذیرش نظرسنجی برای یک رزرو.

    ترتیب بررسی‌ها مهم است: نخست وضعیت اقامت، سپس زمان پایان و در آخر پنجرهٔ
    زمانی — تا پیام خطا همیشه گویا باشد («چرا الان نمی‌شود؟»).
    """
    pk = reservation.pk
    if reservation.status != ReservationStatus.COMPLETED:
        status = reservation.get_status_display()
        return FeedbackEligibility(pk, False, f"این اقامت در وضعیت «{status}» است.")
    now = timezone.now()
    if reservation.ends_at > now:
        return FeedbackEligibility(pk, False, "اقامت هنوز به پایان نرسیده است.")
    if now - reservation.ends_at > timedelta(days=WINDOW_DAYS):
        return FeedbackEligibility(
            pk, False, f"پنجرهٔ نظرسنجی ({WINDOW_DAYS} روز پس از اقامت) بسته شده است."
        )
    feedback = GuestFeedback.objects.filter(reservation_id=pk).first()
    if feedback is not None and feedback.is_submitted:
        return FeedbackEligibility(pk, False, "نظرسنجی این اقامت پیش‌تر ثبت شده است.")
    return FeedbackEligibility(pk, True)


def request_feedback(
    *,
    reservation: Reservation,
    by: Any = None,
    request: HttpRequest | None = None,
    resend: bool = False,
) -> GuestFeedback:
    """درخواست نظرسنجی: ساخت/به‌روزرسانی رکورد و ارسال پیامک با پیوند یکتاشده.

    پیامک هرگز جریان را نمی‌شکند (قرارداد ``send_sms``)؛ حتی اگر روزی ارائه‌دهنده
    از کار بیفتد، رکورد نظرسنجی ساخته می‌شود و کارکنان می‌توانند پیوند را دستی
    بدهند. ``resend`` اجازهٔ ارسال دوباره را می‌دهد بدون آنکه دو رکورد ساخته شود.
    """
    state = eligibility(reservation)
    if not state.eligible:
        raise ConflictError(state.reason or "این اقامت واجد شرایط نظرسنجی نیست.")

    with transaction.atomic():
        feedback, created = GuestFeedback.objects.select_for_update().get_or_create(
            reservation=reservation,
            defaults={"source": GuestFeedback.Source.SMS},
        )
        if feedback.is_submitted:
            raise ConflictError("نظرسنجی این اقامت پیش‌تر ثبت شده است.")
        if feedback.requested_at is not None and not resend:
            logger.info("feedback_request_duplicate", extra={"topic": reservation.code})
            return feedback
        feedback.requested_at = timezone.now()
        feedback.request_count = feedback.request_count + 1
        feedback.save(update_fields=sorted({"requested_at", "request_count", "updated_at"}))

    url = feedback_url(reservation)
    send_sms(
        phone=reservation.guest.phone,
        template_key=SmsTemplateKey.FEEDBACK_REQUEST,
        context={
            "full_name": reservation.guest.display_name or "زائر گرامی",
            "unit": reservation.unit.title,
            "code": reservation.code,
            "url": url,
        },
        dedup_key=f"feedback:{reservation.code}:{feedback.request_count}",
    )
    record(
        AuditEntry(
            action=AuditAction.FEEDBACK_REQUESTED,
            actor_id=getattr(by, "pk", None),
            actor_label=getattr(by, "display_name", "") or "سامانه",
            target_type="reservation",
            target_id=str(reservation.pk),
            metadata={
                "code": reservation.code,
                "attempt": feedback.request_count,
                "created": created,
            },
            request=request,
        )
    )
    logger.info("feedback_requested", extra={"topic": reservation.code})
    return feedback


def submit_feedback(
    *,
    token: str,
    rating: int,
    comment: str = "",
    would_recommend: bool | None = None,
    scores: dict[str, int | None] | None = None,
    request: HttpRequest | None = None,
) -> GuestFeedback:
    """ثبت پاسخ زائر — با اعتبارسنجی کامل و ثبت تغییرناپذیر در حسابرسی.

    توکن ممکن است پیش‌تر برای این رزرو درخواست نشده باشد (مثلاً زائر پیوند را از
    پذیرش گرفته)؛ در آن حالت رکورد با منبع ``web`` ساخته می‌شود.
    """
    reservation = resolve_token(token)
    state = eligibility(reservation)
    if not state.eligible:
        if "ثبت شده است" in state.reason:
            raise ConflictError(state.reason)
        raise FeedbackError(state.reason or "این اقامت واجد شرایط نظرسنجی نیست.")

    if not MIN_RATING <= int(rating) <= MAX_RATING:
        raise FeedbackError("امتیاز باید عددی بین ۱ تا ۵ باشد.")

    payload = dict(scores or {})
    cleaned: dict[str, int | None] = {}
    for field in SUBSCORE_FIELDS:
        raw = payload.get(field)
        if raw is None or str(raw).strip() == "":
            cleaned[field] = None
            continue
        number = int(raw)
        if not MIN_RATING <= number <= MAX_RATING:
            raise FeedbackError("زیرامتیازها باید بین ۱ تا ۵ باشند.")
        cleaned[field] = number

    with transaction.atomic():
        feedback, _ = GuestFeedback.objects.select_for_update().get_or_create(
            reservation=reservation,
            defaults={"source": GuestFeedback.Source.WEB},
        )
        if feedback.is_submitted:
            raise ConflictError("نظرسنجی این اقامت پیش‌تر ثبت شده است.")
        feedback.rating = int(rating)
        feedback.comment = comment.strip()[:COMMENT_MAX_LENGTH]
        feedback.would_recommend = would_recommend
        for field, value in cleaned.items():
            setattr(feedback, field, value)
        feedback.submitted_at = timezone.now()
        feedback.save(
            update_fields=sorted(
                {
                    "rating",
                    "comment",
                    "would_recommend",
                    "submitted_at",
                    "updated_at",
                    *SUBSCORE_FIELDS,
                }
            )
        )

    record(
        AuditEntry(
            action=AuditAction.FEEDBACK_SUBMITTED,
            actor_id=reservation.guest_id,
            actor_label=reservation.guest.display_name or "زائر",
            target_type="reservation",
            target_id=str(reservation.pk),
            metadata={
                "code": reservation.code,
                "rating": feedback.rating,
                "would_recommend": feedback.would_recommend,
                "has_comment": bool(feedback.comment),
            },
            request=request,
        )
    )
    logger.info("feedback_submitted", extra={"topic": reservation.code, "rating": feedback.rating})
    return feedback


@dataclass(frozen=True, slots=True)
class FeedbackBadge:
    """وضعیت نظرسنجی یک رزرو برای تابلوی پذیرش — آمادهٔ نمایش، بدون منطق در قالب."""

    code: str
    submitted: bool = False
    asked: bool = False
    can_send: bool = False
    rating: int | None = None
    label: str = ""


def board_states(reservations: Sequence[Reservation]) -> dict[str, FeedbackBadge]:
    """نگاشت «کد رزرو ⇒ وضعیت نظرسنجی» با **یک** پرسمان برای کل تابلو.

    تابلو روزانه ده‌ها رزرو نشان می‌دهد؛ اگر هر ردیف یک پرس‌وجو بزند، صفحه کند
    می‌شود. پس همهٔ رکوردهای نظرسنجی یک‌بار خوانده و با نگاشت در حافظه تطبیق
    داده می‌شوند.
    """
    codes = [item.code for item in reservations]
    if not codes:
        return {}
    rows = {
        row.reservation.code: row
        for row in GuestFeedback.objects.filter(reservation__code__in=codes).only(
            "rating", "submitted_at", "requested_at", "reservation__code"
        )
    }
    states: dict[str, FeedbackBadge] = {}
    for reservation in reservations:
        row = rows.get(reservation.code)
        submitted = bool(row is not None and row.is_submitted)
        asked = bool(row is not None and row.requested_at is not None)
        state = eligibility(reservation)
        if submitted:
            label = f"نظر ثبت شد — {row.rating if row else ''}"
        elif asked:
            label = "نظرسنجی فرستاده شد؛ در انتظار پاسخ"
        elif state.eligible:
            label = "آمادهٔ درخواست نظرسنجی"
        else:
            label = state.reason
        states[reservation.code] = FeedbackBadge(
            code=reservation.code,
            submitted=submitted,
            asked=asked,
            can_send=state.eligible,
            rating=row.rating if row is not None else None,
            label=label,
        )
    return states


def pending_feedback_days() -> int:
    """پنجرهٔ باز نظرسنجی به روز — برای متن رابط کاربری و مستندات."""
    return WINDOW_DAYS
