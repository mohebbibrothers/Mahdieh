"""تست‌های نظرسنجی پس از اقامت — از پیامک تا میانگین رضایت.

سه پرسش کلیدی این تست‌ها:
    ۱) آیا فقط اقامتِ واقعاً پایان‌یافته نظرسنجی می‌گیرد؟
    ۲) آیا یک نظرسنجی، یک بار و بی‌تکرار ثبت می‌شود؟
    ۳) آیا میانگین رضایت فقط از پاسخ‌های ثبت‌شده ساخته می‌شود (نه از درخواست‌ها)؟
"""

from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.utils import timezone
import pytest
import time_machine

from apps.analytics import insights
from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.core.errors import ConflictError, ValidationError
from apps.notifications.choices import SmsTemplateKey
from apps.notifications.models import SmsDelivery
from apps.notifications.providers import MockSmsProvider
from apps.reservation import feedback as feedback_services, services as reservation_services
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import GuestFeedback, Reservation
from tests.factories import days_ahead, lattice_period

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

WINDOW = feedback_services.WINDOW_DAYS


@pytest.fixture
def stayed_reservation(guest, units, operator):
    """اقامتی که واقعاً انجام و تخلیه شده است — با سفر در زمان، نه دست‌کاری داده.

    مسیر واقعی پیموده می‌شود: چهار روز پیش از اقامت رزرو می‌شود، در زمان اقامت
    پذیرش می‌گیرد و پس از پایان تخلیه می‌شود. اگر ماشین حالت رزرو عوض شود، این
    فیکسچر می‌شکند — که خودش سیگنال ارزشمندی است.
    """
    start, end = lattice_period(start_day=days_ahead(-3), index=0, cells=2)

    with time_machine.travel(start - timedelta(days=4), tick=False):
        reservation = reservation_services.create_reservation(
            guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
        )
    # نکته: خروجی سرویس‌ها دوباره به متغیر بسته می‌شود؛ شیء درون‌حافظه‌ای
    # به‌روز نمی‌شود و بی‌آن، وضعیت کهنه به گام بعد می‌رود.
    with time_machine.travel(start + timedelta(hours=1), tick=False):
        reservation = reservation_services.check_in(reservation=reservation, by=operator)
    with time_machine.travel(end + timedelta(hours=1), tick=False):
        reservation = reservation_services.check_out(reservation=reservation, by=operator)

    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.COMPLETED
    return reservation


def _feedback_deliveries(reservation) -> int:
    """تعداد پیامک‌های نظرسنجی یک اقامت — بدون شمردن پیامک تأیید رزرو."""
    return SmsDelivery.objects.filter(
        template_key=SmsTemplateKey.FEEDBACK_REQUEST,
        dedup_key__startswith=f"feedback:{reservation.code}:",
    ).count()


def test_eligibility_requires_completed_stay(guest, units) -> None:
    """اقامت جاری و آینده، واجد شرایط نظرسنجی نیست."""
    start, end = lattice_period(start_day=days_ahead(4), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    state = feedback_services.eligibility(reservation)
    assert state.eligible is False
    assert "وضعیت" in state.reason


def test_eligibility_window_closes_after_30_days(stayed_reservation) -> None:
    """پس از ۳۰ روز از پایان اقامت، پنجرهٔ نظرسنجی بسته می‌شود."""
    with time_machine.travel(timezone.now() + timedelta(days=WINDOW + 2), tick=False):
        state = feedback_services.eligibility(stayed_reservation)
    assert state.eligible is False
    assert str(WINDOW) in state.reason


def test_request_feedback_sends_sms_with_tokenised_link(stayed_reservation, operator) -> None:
    """درخواست نظرسنجی: پیامک با پیوند امضاشده + ثبت در حسابرسی."""
    feedback = feedback_services.request_feedback(reservation=stayed_reservation, by=operator)
    assert feedback.requested_at is not None
    assert feedback.request_count == 1
    delivery = SmsDelivery.objects.filter(dedup_key=f"feedback:{stayed_reservation.code}:1").first()
    assert delivery is not None
    assert delivery.template_key == SmsTemplateKey.FEEDBACK_REQUEST
    assert "/feedback/" in feedback_services.feedback_url(stayed_reservation)
    assert AuditRecord.objects.filter(action=AuditAction.FEEDBACK_REQUESTED).exists()


def test_request_feedback_is_idempotent_without_resend(stayed_reservation) -> None:
    """درخواست دوباره بدون ``resend``، پیامک تکراری نمی‌فرستد."""
    feedback_services.request_feedback(reservation=stayed_reservation)
    feedback_services.request_feedback(reservation=stayed_reservation)
    row = GuestFeedback.objects.get(reservation=stayed_reservation)
    assert row.request_count == 1
    assert _feedback_deliveries(stayed_reservation) == 1


def test_resend_increments_counter_and_sends_again(stayed_reservation) -> None:
    """یادآوری، شمارنده را بالا می‌برد و پیامک تازه‌ای می‌فرستد (کلید تکراری جدا)."""
    feedback_services.request_feedback(reservation=stayed_reservation)
    feedback_services.request_feedback(reservation=stayed_reservation, resend=True)
    row = GuestFeedback.objects.get(reservation=stayed_reservation)
    assert row.request_count == 2
    assert _feedback_deliveries(stayed_reservation) == 2


def test_request_rejected_for_unfinished_stay(guest, units) -> None:
    """اقامت پایان‌نیافته، پیامک نظرسنجی نمی‌گیرد."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    with pytest.raises(ConflictError):
        feedback_services.request_feedback(reservation=reservation)


def test_token_round_trip_and_tamper_detection(stayed_reservation) -> None:
    """توکن امضاشده فقط با امضای درست باز می‌شود؛ دست‌کاری ⇒ خطای دامنه."""
    token = feedback_services.issue_token(stayed_reservation)
    assert feedback_services.resolve_token(token).pk == stayed_reservation.pk
    with pytest.raises(ValidationError):
        feedback_services.resolve_token(token + "x")
    with pytest.raises(ValidationError):
        feedback_services.resolve_token("not-a-token")


def test_expired_token_is_rejected(stayed_reservation) -> None:
    """توکن کهنه (پس از مهلت ۴۵ روزه) پذیرفته نمی‌شود."""
    token = feedback_services.issue_token(stayed_reservation)
    beyond = timezone.now() + timedelta(days=feedback_services.TOKEN_MAX_AGE_DAYS + 5)
    with time_machine.travel(beyond, tick=False), pytest.raises(ValidationError):
        feedback_services.resolve_token(token)


def test_submit_feedback_persists_scores_and_audits(stayed_reservation) -> None:
    """ثبت پاسخ: امتیاز کلی، زیرامتیازها، پیشنهاد و ردیف حسابرسی."""
    token = feedback_services.issue_token(stayed_reservation)
    feedback = feedback_services.submit_feedback(
        token=token,
        rating=5,
        comment="  پذیرش گرم بود  ",
        would_recommend=True,
        scores={"cleanliness": 5, "hospitality": 4, "facilities": None, "comfort": "3"},
    )
    assert feedback.is_submitted
    assert feedback.rating == 5
    assert feedback.comment == "پذیرش گرم بود"
    assert feedback.cleanliness == 5 and feedback.hospitality == 4
    assert feedback.facilities is None and feedback.comfort == 3
    assert feedback.would_recommend is True
    assert feedback.score_label.startswith("۵")
    assert AuditRecord.objects.filter(action=AuditAction.FEEDBACK_SUBMITTED).exists()


def test_second_submission_is_rejected(stayed_reservation) -> None:
    """یک اقامت، یک نظر: پاسخ دوم خطای تضاد می‌گیرد."""
    token = feedback_services.issue_token(stayed_reservation)
    feedback_services.submit_feedback(token=token, rating=4)
    with pytest.raises(ConflictError):
        feedback_services.submit_feedback(token=token, rating=1)
    assert GuestFeedback.objects.filter(reservation=stayed_reservation).count() == 1


def test_invalid_scores_are_rejected(stayed_reservation) -> None:
    """امتیاز بیرون از بازهٔ ۱ تا ۵ پذیرفته نمی‌شود."""
    token = feedback_services.issue_token(stayed_reservation)
    with pytest.raises(ValidationError):
        feedback_services.submit_feedback(token=token, rating=9)
    with pytest.raises(ValidationError):
        feedback_services.submit_feedback(token=token, rating=3, scores={"cleanliness": 7})


def test_comment_is_truncated_to_safe_length(stayed_reservation) -> None:
    """دیدگاه بسیار بلند بریده می‌شود (سد امنیتی، بی‌خطا)."""
    token = feedback_services.issue_token(stayed_reservation)
    feedback = feedback_services.submit_feedback(
        token=token, rating=3, comment="ب" * (feedback_services.COMMENT_MAX_LENGTH + 500)
    )
    assert len(feedback.comment) == feedback_services.COMMENT_MAX_LENGTH


def test_satisfaction_average_ignores_requests_without_answer(
    stayed_reservation, other_guest, units, operator
) -> None:
    """میانگین رضایت فقط از پاسخ‌ها ساخته می‌شود، نه از درخواست‌های بی‌پاسخ."""
    feedback_services.request_feedback(reservation=stayed_reservation)
    feedback_services.submit_feedback(
        token=feedback_services.issue_token(stayed_reservation),
        rating=5,
        comment="اتاق مرتب بود و پذیرش سریع.",
        would_recommend=True,
        scores={"cleanliness": 4},
    )
    second_start, second_end = lattice_period(start_day=days_ahead(1), index=0, cells=2)
    with time_machine.travel(timezone.now() - timedelta(days=8), tick=False):
        prepared_start, prepared_end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
        second = reservation_services.create_reservation(
            guest=other_guest,
            unit=units["B3"],
            start=prepared_start,
            end=prepared_end,
            guests_count=2,
        )
        second = reservation_services.check_in(reservation=second, by=operator)
        second = reservation_services.check_out(reservation=second, by=operator)
    assert second_start and second_end
    feedback_services.request_feedback(reservation=second)  # درخواست‌شده، بی‌پاسخ

    data = insights.satisfaction()
    assert data["answered"] == 1
    assert data["requested"] == 2
    assert data["average"] == 5.0
    assert data["recommend_rate"] == 1.0
    breakdown = {row["key"]: row for row in data["breakdown"]}
    assert breakdown["cleanliness"]["average"] == 4.0
    assert breakdown["hospitality"]["average"] is None
    assert data["comments"][0]["code"] == stayed_reservation.code


def test_board_states_map_reflects_lifecycle(stayed_reservation, operator) -> None:
    """وضعیت تابلو، مسیر «آماده ⇒ درخواست‌شده ⇒ ثبت‌شده» را درست می‌پیماید."""
    before = feedback_services.board_states([stayed_reservation])[stayed_reservation.code]
    assert before.can_send is True and before.submitted is False

    feedback_services.request_feedback(reservation=stayed_reservation, by=operator)
    asked = feedback_services.board_states([stayed_reservation])[stayed_reservation.code]
    assert asked.asked is True and asked.can_send is True

    feedback_services.submit_feedback(
        token=feedback_services.issue_token(stayed_reservation), rating=4
    )
    done = feedback_services.board_states([stayed_reservation])[stayed_reservation.code]
    assert done.submitted is True and done.can_send is False and done.rating == 4


def test_feedback_page_flow(client: Client, stayed_reservation, operator) -> None:
    """مسیر کاربر: پیامک ⇒ فرم ⇒ سپاس؛ پاسخ دوم ۴۰۹ و توکن نامعتبر ۴۱۰."""
    feedback_services.request_feedback(reservation=stayed_reservation, by=operator)
    token = feedback_services.issue_token(stayed_reservation)

    page = client.get(f"/feedback/{token}/")
    assert page.status_code == 200
    assert "اقامت شما چطور بود" in page.content.decode()

    submitted = client.post(f"/feedback/{token}/", {"rating": "5", "comment": "خوب بود"})
    assert submitted.status_code == 200
    assert "سپاس" in submitted.content.decode()

    again = client.post(f"/feedback/{token}/", {"rating": "1"})
    assert again.status_code == 409

    assert client.get("/feedback/broken-token/").status_code == 410
    assert client.post(f"/feedback/{token}/", {"rating": ""}).status_code == 400


def test_request_page_requires_rating(client: Client, stayed_reservation, operator) -> None:
    """فرم بدون امتیاز پذیرفته نمی‌شود (راهنمای روشن، نه خطای سرور)."""
    token = feedback_services.issue_token(stayed_reservation)
    response = client.post(f"/feedback/{token}/", {"rating": ""})
    assert response.status_code == 400
    assert "امتیاز" in response.content.decode()
    assert not GuestFeedback.objects.filter(reservation=stayed_reservation, rating__isnull=False)


def test_staff_request_endpoint_requires_staff(client: Client, stayed_reservation, guest) -> None:
    """زائر معمولی نمی‌تواند برای دیگران درخواست نظرسنجی بفرستد."""
    client.force_login(guest)
    response = client.post(f"/board/feedback/{stayed_reservation.code}/", {})
    assert response.status_code == 403
    assert not GuestFeedback.objects.exists()


def test_staff_request_endpoint_sends_and_redirects(
    client: Client, stayed_reservation, operator
) -> None:
    """کارکنان با یک POST، پیامک نظرسنجی را می‌فرستند و به تابلو برمی‌گردند."""
    client.force_login(operator)
    response = client.post(
        f"/board/feedback/{stayed_reservation.code}/",
        {"board_day": timezone.localdate().isoformat()},
    )
    assert response.status_code == 302
    assert f"day={timezone.localdate().isoformat()}" in response["Location"]
    assert GuestFeedback.objects.filter(reservation=stayed_reservation).exists()
    assert MockSmsProvider.outbox  # پیامک در صندوق ماک نشسته است


def test_command_sends_only_to_finished_stays(stayed_reservation, guest, units) -> None:
    """فرمان زمان‌بندی‌شده فقط اقامت‌های پایان‌یافته را هدف می‌گیرد."""
    from io import StringIO

    from django.core.management import call_command

    start, end = lattice_period(start_day=days_ahead(5), index=0, cells=2)
    pending = reservation_services.create_reservation(
        guest=guest, unit=units["B3"], start=start, end=end, guests_count=2
    )

    out = StringIO()
    call_command("send_feedback_requests", "--dry-run", stdout=out)
    listed = out.getvalue()
    assert stayed_reservation.code in listed
    assert pending.code not in listed
    assert GuestFeedback.objects.count() == 0

    call_command("send_feedback_requests", stdout=StringIO())
    assert GuestFeedback.objects.filter(reservation=stayed_reservation).exists()
    assert not GuestFeedback.objects.filter(reservation=pending).exists()


def test_command_respects_limit(stayed_reservation, other_guest, units, operator) -> None:
    """``--limit`` سقف ارسال هر اجرا را می‌بندد."""
    from io import StringIO

    from django.core.management import call_command

    with time_machine.travel(timezone.now() - timedelta(days=9), tick=False):
        prepared_start, prepared_end = lattice_period(start_day=days_ahead(8), index=0, cells=2)
        second = reservation_services.create_reservation(
            guest=other_guest,
            unit=units["B3"],
            start=prepared_start,
            end=prepared_end,
            guests_count=2,
        )
        second = reservation_services.check_in(reservation=second, by=operator)
        second = reservation_services.check_out(reservation=second, by=operator)

    call_command("send_feedback_requests", "--limit", "1", "--after-hours", "1", stdout=StringIO())
    assert GuestFeedback.objects.count() == 1


def test_ready_reservations_do_not_appear_in_command(stayed_reservation) -> None:
    """پس از ثبت پاسخ، اقامت از فهرست فرمان حذف می‌شود (بی‌نیاز از پیامک دوباره)."""
    from io import StringIO

    from django.core.management import call_command

    feedback_services.submit_feedback(
        token=feedback_services.issue_token(stayed_reservation), rating=5
    )
    out = StringIO()
    call_command("send_feedback_requests", stdout=out)
    assert "پیدا نشد" in out.getvalue()


def test_history_of_stay_is_kept_after_feedback(stayed_reservation) -> None:
    """نظرسنجی، رزرو را دست‌کاری نمی‌کند (سابقهٔ اقامت دست‌نخورده می‌ماند)."""
    snapshot = Reservation.objects.values("code", "status", "cells_count").get(
        pk=stayed_reservation.pk
    )
    feedback_services.submit_feedback(
        token=feedback_services.issue_token(stayed_reservation), rating=2
    )
    assert (
        Reservation.objects.values("code", "status", "cells_count").get(pk=stayed_reservation.pk)
        == snapshot
    )
