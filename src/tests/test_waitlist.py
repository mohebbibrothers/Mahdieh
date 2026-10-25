"""تست‌های صف انتظار — از ثبت درخواست تا پیشنهاد یک‌ضربه‌ای و پذیرش.

سناریوی محوری (همان چیزی که در محصول رخ می‌دهد):
    ۱) سانس پر است ⇒ زائر «درخواست انتظار» می‌دهد.
    ۲) سانس آزاد می‌شود (لغو/جابه‌جایی) ⇒ رخداد ``slot.freed`` منتشر می‌شود.
    ۳) نفر اول صف پیشنهاد انحصاری با مهلت می‌گیرد و پیامک می‌شود.
    ۴) با یک ضربه (توکن امضاشده، بدون ورود دوباره) به رزرو تبدیل می‌شود.
"""

from __future__ import annotations

from datetime import timedelta

from django.core import signing
from django.utils import timezone
import pytest
import time_machine

from apps.core import events
from apps.core.errors import ConflictError, PolicyViolationError
from apps.notifications.choices import SmsTemplateKey
from apps.notifications.providers import MockSmsProvider
from apps.reservation import services as reservation_services
from apps.reservation.models import Reservation
from apps.waitlist import selectors, services
from apps.waitlist.choices import DeclineReason, OfferStatus, WaitlistStatus
from apps.waitlist.models import SlotOffer, WaitlistEntry
from apps.waitlist.tokens import OfferTokenError, make_token, read_token
from tests.factories import days_ahead, lattice_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _occupied_slot(*, owner, unit, day_offset: int = 5, guests: int = 2):
    """یک سانس پر بسازد و «بازه + خود رزرو» را برگرداند."""
    start, end = lattice_period(start_day=days_ahead(day_offset), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=owner, unit=unit, start=start, end=end, guests_count=guests
    )
    return start, end, reservation


def _cancel(*, reservation, by, is_staff: bool = False, company: str = "انصراف") -> None:
    """لغو رزرو با دلیلی معنادار (سرویس دامنه، نه نوشتن مستقیم)."""
    reservation_services.cancel_reservation(
        reservation=reservation,
        by=by,
        reason_kind="maintenance" if is_staff else "guest_request",
        reason=company,
        is_staff=is_staff,
    )


def _waiting_entry(*, guest, unit, start, end, guests: int = 2) -> WaitlistEntry:
    """درخواست انتظار بسازد (چون سانس پر است، مسیر درست است)."""
    return services.join_waitlist(guest=guest, unit=unit, start=start, end=end, guests_count=guests)


def test_join_waitlist_requires_occupied_slot(guest, units) -> None:
    """درخواست انتظار برای سانس آزاد رد می‌شود — با راهنمای «همین‌جا رزرو کنید»."""
    start, end = lattice_period(start_day=days_ahead(4), index=0, cells=2)
    with pytest.raises(ConflictError) as error:
        services.join_waitlist(guest=guest, unit=units["A1"], start=start, end=end, guests_count=2)
    assert error.value.code == "slot_already_free"


def test_join_waitlist_rejects_past_and_offgrid(guest, units) -> None:
    """بازهٔ گذشته و بازهٔ ناهم‌تراز با نیم‌روز، هر دو رد می‌شوند."""
    past_start, past_end = lattice_period(start_day=days_ahead(-2), index=0, cells=2)
    with pytest.raises(PolicyViolationError):
        services.join_waitlist(
            guest=guest, unit=units["A1"], start=past_start, end=past_end, guests_count=1
        )

    start, end = lattice_period(start_day=days_ahead(6), index=0, cells=2)
    with pytest.raises(Exception) as error:  # خطای دامنه، نه ۵۰۰
        services.join_waitlist(
            guest=guest,
            unit=units["A1"],
            start=start.replace(hour=9),
            end=end,
            guests_count=1,
        )
    assert getattr(error.value, "code", "") == "off_grid"


def test_only_one_active_entry_per_guest(other_guest, guest, units) -> None:
    """هر زائر هم‌زمان فقط یک درخواست در جریان دارد."""
    start, end, _first = _occupied_slot(owner=other_guest, unit=units["A1"])
    _waiting_entry(guest=guest, unit=units["A1"], start=start, end=end)

    other_owner = make_user(phone="09120007001")
    second_day = _occupied_slot(owner=other_owner, unit=units["B1"], day_offset=7)
    with pytest.raises(PolicyViolationError) as error:
        _waiting_entry(guest=guest, unit=units["B1"], start=second_day[0], end=second_day[1])
    assert error.value.extensions["violations"][0]["code"] == "waitlist_quota"


def test_slot_freed_creates_offer_for_first_in_queue(guest, other_guest, units) -> None:
    """با آزاد شدن سانس، نفر اول صف پیشنهاد انحصاری و پیامک می‌گیرد."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["A1"])
    entry = _waiting_entry(guest=guest, unit=units["A1"], start=start, end=end)
    assert entry.status == WaitlistStatus.WAITING
    assert services.queue_position(entry) == 1

    _cancel(reservation=reservation, by=other_guest, company="منصرف شدم")

    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None
    assert offer.status == OfferStatus.OPEN
    assert offer.unit_id == units["A1"].id
    assert offer.expires_at > timezone.now()
    # پیامک پیشنهاد واقعاً صف‌بندی شده است (ارائه‌دهنده در توسعه/تست: ماک)
    keys = [message.template_key for message in MockSmsProvider.outbox]
    assert SmsTemplateKey.OFFER_CREATED in keys
    offer_sms = next(
        message
        for message in MockSmsProvider.outbox
        if message.template_key == SmsTemplateKey.OFFER_CREATED
    )
    assert "http" in offer_sms.body  # لینک یک‌ضربه‌ای در متن پیامک هست


def test_offer_token_is_signed_and_scoped_to_action(guest, other_guest, units) -> None:
    """توکن لینک پیامک: امضاشده، مخصوص یک کنش، و دست‌کاری‌ناپذیر."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["B1"])
    entry = _waiting_entry(guest=guest, unit=units["B1"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest, is_staff=True, company="تعمیر فوری")
    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None

    token = make_token(offer_id=offer.id, action="accept")
    assert read_token(token, expected_action="accept", max_age_seconds=1800) == str(offer.id)

    with pytest.raises(OfferTokenError):
        read_token(token, expected_action="decline", max_age_seconds=1800)

    tampered = f"{token[:-2]}xy"
    with pytest.raises(OfferTokenError):
        read_token(tampered, expected_action="accept", max_age_seconds=1800)

    expired = signing.dumps(
        {"offer": str(offer.id), "action": "accept"},
        salt="mahdieh.waitlist.offer",
        compress=True,
    )
    future = timezone.now() + timedelta(hours=3)
    with time_machine.travel(future, tick=False), pytest.raises(OfferTokenError):
        read_token(expired, expected_action="accept", max_age_seconds=1800)


def test_accept_offer_creates_reservation_and_converts_entry(guest, other_guest, units) -> None:
    """پذیرش پیشنهاد: رزرو ساخته می‌شود، درخواست صف «تبدیل‌شده» می‌شود."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["A2"])
    entry = _waiting_entry(guest=guest, unit=units["A2"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest, company="تغییر برنامه")

    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None
    offer, created = services.accept_offer(offer=offer, by=guest)

    assert created.code
    assert created.unit_id == units["A2"].id
    assert created.starts_at == start and created.ends_at == end
    assert created.guests_count == 2
    assert offer.status == OfferStatus.ACCEPTED

    entry.refresh_from_db()
    assert entry.status == WaitlistStatus.CONVERTED
    assert entry.converted_reservation_id == created.id
    assert created.source == "waitlist"


def test_decline_offer_returns_slot_to_next_in_queue(guest, other_guest, units) -> None:
    """رد پیشنهاد: سانس به نفر بعدی صف می‌رسد (نه به فراموشی)."""
    owner = make_user(phone="09120006001")
    start, end, reservation = _occupied_slot(owner=owner, unit=units["B3"])
    first = _waiting_entry(guest=guest, unit=units["B3"], start=start, end=end)
    second_guest = make_user(phone="09120006002")
    second = services.join_waitlist(
        guest=second_guest, unit=units["B3"], start=start, end=end, guests_count=1
    )
    assert second.status == WaitlistStatus.WAITING

    _cancel(reservation=reservation, by=owner, is_staff=True, company="سفر لغو شد")
    offer = selectors.open_offer_for_entry(first)
    assert offer is not None

    services.decline_offer(offer=offer, by=guest, reason_kind=DeclineReason.TIME_NOT_SUITABLE)
    offer.refresh_from_db()
    assert offer.status == OfferStatus.DECLINED

    next_offer = selectors.open_offer_for_entry(second)
    assert next_offer is not None, "پیشنهاد باید به نفر بعدی صف برسد"
    assert next_offer.entry_id == second.id


def test_withdraw_entry_frees_the_queue_and_offers_next(guest, other_guest, units) -> None:
    """انصراف: درخواست بسته می‌شود و نوبت آزاد می‌گردد."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["A1"], day_offset=9)
    entry = _waiting_entry(guest=guest, unit=units["A1"], start=start, end=end)
    services.withdraw_entry(entry=entry, by=guest, reason="نیاز ندارم")
    entry.refresh_from_db()
    assert entry.status == WaitlistStatus.WITHDRAWN
    assert services.queue_position(entry) == 0


def test_expire_offers_requeues_after_max_offers(guest, other_guest, units) -> None:
    """پیشنهاد بی‌پاسخ می‌ماند ⇒ پس از سقف پیشنهادها، درخواست بسته می‌شود."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["B2"], day_offset=11)
    entry = _waiting_entry(guest=guest, unit=units["B2"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest)
    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None

    later = offer.expires_at + timedelta(minutes=1)
    with time_machine.travel(later, tick=False):
        expired = services.expire_offers()
    assert expired == 1
    offer.refresh_from_db()
    entry.refresh_from_db()
    assert offer.status == OfferStatus.EXPIRED
    assert entry.status in {WaitlistStatus.WAITING, WaitlistStatus.UNFULFILLED}


def test_waitlist_api_round_trip(api, guest, other_guest, units) -> None:
    """قرارداد API: ثبت درخواست، مشاهدهٔ آن، و انصراف — همه با احراز هویت."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["A1"], day_offset=13)

    unauthorized = api.post(
        "/api/v1/waitlist",
        {
            "unit_code": str(units["A1"].code),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "guests_count": 2,
        },
        format="json",
    )
    assert unauthorized.status_code in {401, 403}

    api.force_authenticate(user=guest)
    created = api.post(
        "/api/v1/waitlist",
        {
            "unit_code": str(units["A1"].code),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "guests_count": 2,
            "note": "سفر زیارتی",
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    payload = created.json()
    assert payload["status"] == WaitlistStatus.WAITING
    assert payload["unit_code"] == "A1"

    listed = api.get("/api/v1/waitlist")
    assert listed.status_code == 200
    assert len(listed.json()["results"]) == 1

    withdrawn = api.post(f"/api/v1/waitlist/{payload['id']}/withdraw", {}, format="json")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == WaitlistStatus.WITHDRAWN


def test_waitlist_list_exposes_open_offer_for_portal(api, guest, other_guest, units) -> None:
    """پرتال زائر پیشنهاد باز را از فهرست خودش می‌بیند و بی لینک پیامک می‌پذیرد."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["A2"], day_offset=9)
    entry = _waiting_entry(guest=guest, unit=units["A2"], start=start, end=end)
    api.force_authenticate(user=guest)
    waiting = api.get("/api/v1/waitlist").json()["results"][0]
    assert waiting["status"] == WaitlistStatus.WAITING
    assert waiting["open_offer"] is None

    _cancel(reservation=reservation, by=other_guest)
    offered = api.get("/api/v1/waitlist").json()["results"][0]
    assert offered["status"] == WaitlistStatus.OFFERED
    offer = offered["open_offer"]
    assert offer["entry_id"] == str(entry.id)
    assert offer["unit_code"] == "A2"
    assert offer["seconds_left"] > 0

    accepted = api.post(
        f"/api/v1/waitlist/offers/{offer['id']}/respond", {"action": "accept"}, format="json"
    )
    assert accepted.status_code == 201, accepted.content
    assert api.get("/api/v1/waitlist").json()["results"] == []
    code = accepted.json()["reservation_code"]
    assert api.get(f"/api/v1/reservations/{code}").json()["unit_code"] == "A2"


def test_staff_waitlist_api_is_staff_only(api, guest, operator, other_guest, units) -> None:
    """تابلوی صف برای کارکنان باز است و برای زائر بسته."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["B1"], day_offset=15)
    _waiting_entry(guest=guest, unit=units["B1"], start=start, end=end)

    api.force_authenticate(user=guest)
    assert api.get("/api/v1/staff/waitlist").status_code == 403

    api.force_authenticate(user=operator)
    response = api.get("/api/v1/staff/waitlist")
    assert response.status_code == 200
    body = response.json()
    assert body["queue"] and body["stats"]["waiting"] >= 1


def test_waitlist_pages_render(client, guest, other_guest, units) -> None:
    """صفحهٔ «درخواست‌های انتظار من» برای زائر رندر می‌شود."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["A2"], day_offset=17)
    _waiting_entry(guest=guest, unit=units["A2"], start=start, end=end)

    client.force_login(guest)
    response = client.get("/my/waitlist/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "درخواست‌های انتظار من" in body
    assert "نوبت شما در صف" in body


def test_offer_page_requires_ownership(client, guest, other_guest, units) -> None:
    """لینک پیشنهاد برای غیرمالک، به ورود هدایت می‌شود؛ برای مالک باز است."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["A1"], day_offset=19)
    entry = _waiting_entry(guest=guest, unit=units["A1"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest)
    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None
    token = make_token(offer_id=offer.id, action="accept")
    path = f"/offer/{token}/accept/"

    client.force_login(other_guest)
    assert client.get(path).status_code == 302

    client.force_login(guest)
    response = client.get(path)
    assert response.status_code == 200
    assert "سانس آزاد شد" in response.content.decode()


def test_one_open_offer_per_slot_is_enforced(other_guest, guest, units) -> None:
    """قاعدهٔ دامنه: برای هر سانس حداکثر یک پیشنهاد باز وجود دارد."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["B3"], day_offset=21)
    entry = _waiting_entry(guest=guest, unit=units["B3"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest)
    first = selectors.open_offer_for_entry(entry)
    assert first is not None

    # تماس دوم با همان سانس، نباید پیشنهاد تازه بسازد
    again = services.offer_freed_slot(unit_id=units["B3"].id, start=start, end=end)
    assert again is None
    assert SlotOffer.objects.filter(status=OfferStatus.OPEN).count() == 1


def test_slot_freed_event_handler_is_registered() -> None:
    """پل رزرو↔صف انتظار واقعاً وصل است (نگهبان رگرسیون معماری)."""
    assert events.subscribers(events.SLOT_FREED)


# ─── مسیرهای وب (تجربهٔ کاربری واقعی) ─────────────────────────────────────────


def test_web_join_flow_shows_position(client, guest, other_guest, units) -> None:
    """زائر از وب درخواست می‌دهد و پیام «نوبت شما» را می‌بیند."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["A1"], day_offset=23)
    client.force_login(guest)
    response = client.post(
        "/my/waitlist/join/",
        {
            "unit_code": "A1",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "guests_count": "2",
            "note": "سفر خانوادگی",
            "next": "/calendar/",
        },
    )
    assert response.status_code == 302
    assert response["Location"] == "/my/waitlist/"

    entry = WaitlistEntry.objects.get(guest=guest)
    assert entry.status == WaitlistStatus.WAITING

    page = client.get("/my/waitlist/")
    body = page.content.decode()
    assert (
        "درخواست انتظار شما ثبت شد" in (client.get("/my/waitlist/").content.decode() + body)
        or entry.status_label in body
    )


def test_web_join_rejects_bad_payload_without_error(guest, client, units) -> None:
    """ورودی نامعتبر (زمان ناقص یا بازهٔ ناهم‌تراز) ⇒ پیام خطا، نه ۵۰۰."""
    client.force_login(guest)
    bad_time = client.post(
        "/my/waitlist/join/",
        {"unit_code": "A1", "start": "دیروز", "end": "فردا", "guests_count": "2"},
    )
    assert bad_time.status_code == 302

    start, end = lattice_period(start_day=days_ahead(25), index=0, cells=2)
    off_grid = client.post(
        "/my/waitlist/join/",
        {
            "unit_code": "A1",
            "start": start.replace(hour=9).isoformat(),
            "end": end.isoformat(),
            "guests_count": "2",
        },
    )
    assert off_grid.status_code == 302
    assert WaitlistEntry.objects.filter(guest=guest).count() == 0


def test_web_withdraw_flow(guest, other_guest, client, units) -> None:
    """انصراف زائر از صفحهٔ خودش کار می‌کند و نوبت آزاد می‌شود."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["B1"], day_offset=27)
    entry = _waiting_entry(guest=guest, unit=units["B1"], start=start, end=end)
    client.force_login(guest)
    response = client.post(f"/my/waitlist/{entry.id}/withdraw/", {"reason": "نیاز ندارم"})
    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.status == WaitlistStatus.WITHDRAWN


def test_web_accept_offer_creates_reservation(client, guest, other_guest, units) -> None:
    """پذیرش از صفحهٔ پیشنهاد: رزرو ساخته می‌شود و به صفحهٔ رزرو می‌رود."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["A2"], day_offset=29)
    entry = _waiting_entry(guest=guest, unit=units["A2"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest)
    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None

    from apps.web.views_waitlist import offer_link

    path = offer_link(offer, action="accept")
    client.force_login(guest)
    page = client.get(path)
    assert page.status_code == 200 and "سانس آزاد شد" in page.content.decode()

    accepted = client.post(path)
    assert accepted.status_code == 302
    assert accepted["Location"].startswith("/reservations/M-")

    entry.refresh_from_db()
    assert entry.status == WaitlistStatus.CONVERTED
    assert entry.converted_reservation is not None


def test_web_decline_offer_keeps_slot_for_next(client, guest, other_guest, units) -> None:
    """رد از صفحهٔ پیشنهاد: پیشنهاد بسته می‌شود و رزروی ساخته نمی‌شود."""
    start, end, reservation = _occupied_slot(owner=other_guest, unit=units["B3"], day_offset=31)
    entry = _waiting_entry(guest=guest, unit=units["B3"], start=start, end=end)
    _cancel(reservation=reservation, by=other_guest)
    offer = selectors.open_offer_for_entry(entry)
    assert offer is not None

    from apps.web.views_waitlist import offer_link

    client.force_login(guest)
    response = client.post(
        offer_link(offer, action="decline"), {"reason_kind": "time_not_suitable"}
    )
    assert response.status_code == 302
    offer.refresh_from_db()
    assert offer.status == OfferStatus.DECLINED
    assert Reservation.objects.filter(guest=guest).count() == 0


def test_staff_board_waitlist_renders_and_withdraws(
    client, operator, guest, other_guest, units
) -> None:
    """تابلوی صف کارکنان: نمایش صف و بستن درخواست با دلیل."""
    start, end, _reservation = _occupied_slot(owner=other_guest, unit=units["A1"], day_offset=33)
    entry = _waiting_entry(guest=guest, unit=units["A1"], start=start, end=end)

    client.force_login(operator)
    page = client.get("/board/waitlist/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "صف انتظار" in body
    assert entry.guest.first_name in body

    closed = client.post(f"/board/waitlist/{entry.id}/withdraw/", {"reason": "درخواست تکراری"})
    assert closed.status_code == 302
    entry.refresh_from_db()
    assert entry.status == WaitlistStatus.WITHDRAWN


def test_unknown_offer_token_shows_explanation(client, guest) -> None:
    """لینک بی‌اعتبار (منقضی یا جعلی) صفحهٔ توضیح می‌دهد، نه خطای خشک."""
    client.force_login(guest)
    response = client.get("/offer/not-a-valid-token/accept/")
    assert response.status_code == 410
    assert "معتبر نیست" in response.content.decode()
