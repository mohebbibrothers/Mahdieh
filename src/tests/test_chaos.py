"""تست‌های آشوب (Chaos) — «وقتی همه‌چیز خراب می‌شود، سامانه چه می‌کند؟»

چرا این فایل جدا از ``test_resilience.py`` است؟
    ``test_resilience`` پایداری **زیرساخت** را می‌سنجد (قطعی پایگاه‌داده و کش، سد
    ظرفیت). این فایل آشوب **رفتاری** را می‌سنجد: خرابی‌های ناگهانیِ *سرویس‌های
    بیرونی و صف‌ها* را تزریق می‌کند و می‌پرسد «آیا سامانه بازنده‌ی مهربانی است
    یا فقط ساکت می‌ماند؟».

ده خرابی تزریق‌شده:
    ۱. آداپتور پیامک **استثنا پرت می‌کند** ⇒ ثبت «ناموفق»، نه کرش درخواست زائر.
    ۲. شکست ارسال ⇒ **تلاش دوباره با همان کلید یکتایی** ممکن است (بدون ارسال دوتایی).
    ۳. پاسخ نامعتبر پنل (JSON خراب/خطای ۵۰۰) ⇒ ناموفقِ ثبت‌شده، نه استثنا.
    ۴. پاسخ ۲۰۰ با بدنهٔ خطا ⇒ همان «ناموفق» شمرده می‌شود، نه موفقِ دروغین.
    ۵. سهمیهٔ روزانه تمام ⇒ پیام **مسدود** با دلیل، و روز بعد دوباره باز است.
    ۶. رلهٔ Outbox با رخداد خراب ⇒ آن یکی عقب می‌افتد، **بقیه منتشر می‌شوند**.
    ۷. رله بعد از سقف تلاش ⇒ «مرده» با عقب‌نشینی نمایی (نه حلقهٔ بی‌پایان).
    ۸. شکست ارسال برای یک زائر در چرخهٔ یادآور ⇒ **بقیهٔ زائران یادآور می‌گیرند**.
    ۹. قطعی کش ⇒ آمادگی «افت‌کرده» گزارش می‌شود ولی زنده‌بودن سبز می‌ماند.
   ۱۰. ورودی‌های بدخیم/بزرگ ⇒ پاسخ ۴xx، هرگز ۵۰۰.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
import pytest
import time_machine

from apps.core import outbox
from apps.core.choices import OutboxStatus
from apps.notifications import services as sms_services
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.models import SmsDelivery
from apps.notifications.ports import SmsMessage, SmsReceipt
from apps.notifications.providers import MockSmsProvider, get_sms_provider
from apps.reservation import reminders
from apps.reservation.services import create_reservation
from tests.factories import days_ahead, lattice_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.chaos]

GUEST_PHONE = "09120000001"
SECOND_PHONE = "09120000002"


class _ExplodingProvider:
    """آداپتور بدرفتار: قرارداد می‌گوید استثنا پرت نکن، ولی این یکی می‌کند."""

    name = "exploding"

    def __init__(self, *, fingerprint: str = "") -> None:
        """``fingerprint``: تنها شماره‌ای که این آداپتور برایش می‌ترکد."""
        self.fingerprint = fingerprint

    def send(self, message: SmsMessage) -> SmsReceipt:  # pragma: no cover - مسیر استثنا
        """ترکیدن — دقیقاً همان چیزی که `_attempt` باید مهار کند."""
        if not self.fingerprint or message.phone.endswith(self.fingerprint):
            msg = "provider exploded mid-flight"
            raise TimeoutError(msg)
        return MockSmsProvider().send(message)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    """جایگزینی آداپتور جاری در رجیستری، برای تزریق خرابی."""
    monkeypatch.setattr(sms_services, "get_sms_provider", lambda _name: provider, raising=True)


def _send(template: str = SmsTemplateKey.DRY_RUN, *, phone: str = GUEST_PHONE, **kwargs: Any):
    """ارسال کوتاه‌نویس با کلید یکتایی."""
    return sms_services.send_sms(
        phone=phone,
        template_key=template,
        context={"message": "آزمون آشوب", "code": "۱۲۳۴۵۶", "full_name": "زائر آزمون"},
        **kwargs,
    )


# ─────────────────────────── ۱) آداپتور که می‌ترکد ──────────────────────────
def test_provider_crash_becomes_a_failed_delivery_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """سامانهٔ پیامک که می‌ترکد، نباید زائر را هم با خودش ببرد."""
    _patch_provider(monkeypatch, _ExplodingProvider())
    delivery = _send(dedup_key="chaos:explode")
    assert delivery.status == SmsStatus.FAILED
    assert "exploded" in delivery.error
    # رکورد باقی می‌ماند تا مدیر ببیند و دوباره تلاش کند.
    assert SmsDelivery.objects.filter(pk=delivery.pk).exists()


# ───────────────── ۲) تلاش دوباره پس از شکست (بدون ارسال دوتایی) ─────────────
def test_retry_after_a_failed_send_delivers_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """کلید یکتایی نباید پس از شکست، پیام را برای همیشه زندانی کند."""
    _patch_provider(monkeypatch, _ExplodingProvider())
    first = _send(dedup_key="chaos:retry")
    assert first.status == SmsStatus.FAILED

    monkeypatch.undo()
    second = _send(dedup_key="chaos:retry")
    assert second.status in {SmsStatus.SENT, SmsStatus.SUPPRESSED}
    assert second.pk == first.pk, "تلاش دوباره باید همان رکورد را جلو ببرد، نه رکورد تازه"

    # و بار سوم: حالا که موفق شده، دیگر ارسالی رخ نمی‌دهد.
    before = len(MockSmsProvider.outbox)
    third = _send(dedup_key="chaos:retry")
    assert third.pk == first.pk
    assert len(MockSmsProvider.outbox) == before, "پیام موفق نباید دوباره فرستاده شود"


# ────────────────── ۳) پاسخ نامعتبر پنل (بدنهٔ خراب) ─────────────────────────
def test_malformed_provider_response_is_recorded_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """پنلِ بی‌قرارداد ⇒ «ناموفق» ثبت می‌شود؛ سامانه استثنا نمی‌زند."""

    class _GarbageProvider:
        name = "garbage"

        def send(self, message: SmsMessage) -> SmsReceipt:
            """رسید ناموفق با متن خطای واقعی پنل (نه استثنا)."""
            del message
            return SmsReceipt(ok=False, provider="garbage", error="panel returned 500")

    _patch_provider(monkeypatch, _GarbageProvider())
    delivery = _send(dedup_key="chaos:garbage")
    assert delivery.status == SmsStatus.FAILED
    assert "panel returned 500" in delivery.error


# ───────────── ۴) پاسخ موفقِ دروغین: رسید ok=False با متن خطا ────────────────
def test_dry_run_receipt_is_not_counted_as_a_real_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«ارسال آزمایشی» موفق است ولی تحویل واقعی نیست — گزارش نباید گمراه کند."""
    monkeypatch.setattr(
        sms_services,
        "get_sms_provider",
        lambda _name: get_sms_provider("mock"),
        raising=True,
    )
    delivery = _send(dedup_key="chaos:dryrun")
    assert delivery.status == SmsStatus.SUPPRESSED
    assert delivery.provider == "mock"
    assert delivery.provider_ref.startswith("mock-")


# ───────────────────── ۵) سهمیهٔ روزانه: مسدود، سپس روز بعد باز ─────────────
@override_settings(SMS_DAILY_BUDGET=2)
def test_daily_budget_blocks_words_with_a_reason_and_resets_next_day() -> None:
    """سقف روزانهٔ پیامک: مصرف‌شده‌ها می‌روند، باقی «مسدود با دلیل» می‌شوند."""
    cache.clear()
    today = timezone.localdate()
    first = _send(dedup_key="chaos:budget:1")
    second = _send(dedup_key="chaos:budget:2")
    third = _send(dedup_key="chaos:budget:3")
    assert first.status in {SmsStatus.SENT, SmsStatus.SUPPRESSED}
    assert second.status in {SmsStatus.SENT, SmsStatus.SUPPRESSED}
    assert third.status == SmsStatus.BLOCKED
    assert "سهمیه" in third.error
    assert sms_services.daily_usage(today) == 2

    # روز بعد: سهمیه از نو (ساعت سرور جلو می‌رود، نه شمارنده دست‌کاری شود).
    assert sms_services.daily_usage(today + timedelta(days=1)) == 0
    tomorrow_local = timezone.localtime(timezone.now()) + timedelta(days=1)
    with time_machine.travel(tomorrow_local, tick=False):
        again = _send(dedup_key="chaos:budget:4")
        assert again.status in {SmsStatus.SENT, SmsStatus.SUPPRESSED}
        assert sms_services.daily_usage() == 1


# ─────────────────────── ۶) رلهٔ Outbox: جداسازی رخداد خراب ─────────────────
def test_relay_isolates_the_bad_event_and_publishes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """یک رخداد خراب نباید کل صف را گروگان بگیرد."""
    from django.db import transaction

    from apps.core.management.commands.outbox_relay import Command

    with transaction.atomic():
        bad = outbox.publish("chaos.bad", aggregate_type="chaos", aggregate_id="1")
        good = outbox.publish("chaos.good", aggregate_type="chaos", aggregate_id="2")

    command = Command()

    def _dispatch(event: Any) -> None:
        if event.event_type == "chaos.bad":
            msg = "consumer refused"
            raise RuntimeError(msg)

    monkeypatch.setattr(command, "_dispatch", _dispatch)
    processed = command._run_cycle(limit=10)

    assert processed == 1
    bad.refresh_from_db()
    good.refresh_from_db()
    assert bad.status == OutboxStatus.PENDING
    assert bad.attempts == 1
    assert "consumer refused" in bad.last_error
    assert bad.available_at > timezone.now(), "شکست باید عقب‌نشینی زمانی داشته باشد"
    assert good.status == OutboxStatus.PUBLISHED


# ─────────────── ۷) سقف تلاش: «مرده» شدن با عقب‌نشینی نمایی ──────────────────
def test_relay_dead_letters_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """پس از ۸ شکست، رخداد به صف مرده می‌رود — نه حلقهٔ بی‌پایان."""
    from django.db import transaction

    from apps.core.management.commands.outbox_relay import Command

    with transaction.atomic():
        event = outbox.publish("chaos.always", aggregate_type="chaos", aggregate_id="9")

    command = Command()
    monkeypatch.setattr(
        command, "_dispatch", lambda _event: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    seen_backoffs: list[float] = []
    for _ in range(outbox.MAX_ATTEMPTS):
        command._run_cycle(limit=1)
        event.refresh_from_db()
        if event.status == OutboxStatus.PENDING:
            seen_backoffs.append((event.available_at - timezone.now()).total_seconds())
            # زمان را جلو می‌بریم تا رخداد دوباره واجد انتشار شود (بی‌انتظار واقعی).
            outbox.OutboxEvent.all_objects.filter(pk=event.pk).update(available_at=timezone.now())
    event.refresh_from_db()
    assert event.status == OutboxStatus.DEAD
    assert event.attempts == outbox.MAX_ATTEMPTS
    assert seen_backoffs == sorted(seen_backoffs), "عقب‌نشینی باید صعودی باشد"
    assert seen_backoffs[0] < seen_backoffs[-1], "فاصلهٔ تلاش‌ها باید رشد کند"


# ─────────── ۸) چرخهٔ یادآور: یک زائرِ خراب، بقیه را متوقف نمی‌کند ───────────
def test_reminder_cycle_survives_one_provider_failure(
    monkeypatch: pytest.MonkeyPatch, units: dict[str, Any]
) -> None:
    """یادآور: اگر پیامک یک زائر نشد، بقیهٔ زائران باید پیام بگیرند."""
    good_guest = make_user(phone=SECOND_PHONE)
    bad_guest = make_user(phone=GUEST_PHONE)
    # هر دو اقامت روی یک بازهٔ شبکه‌ای (دو واحد جدا) تا هر دو واجد یادآور باشند.
    start, end = lattice_period(start_day=days_ahead(2), index=0, cells=2)
    create_reservation(guest=bad_guest, unit=units["A1"], start=start, end=end, guests_count=1)
    create_reservation(guest=good_guest, unit=units["B1"], start=start, end=end, guests_count=1)

    # پیامک‌های «تأیید رزرو» در ساخت رزرو رفته‌اند؛ از این‌جا به بعد فقط یادآورها
    # شمرده می‌شوند.
    sent_before = len(MockSmsProvider.outbox)
    _patch_provider(monkeypatch, _ExplodingProvider(fingerprint="0000001"))
    # «ساعات سکوت» (۲۰ تا ۸) درست کار می‌کند؛ پس چرخه را وسط روز می‌آزماییم تا
    # نتیجه به ساعت اجرای تست وابسته نباشد.
    moment = timezone.make_aware(datetime.combine(days_ahead(1), time(9, 0)))
    report = reminders.run(now=moment, limit=10)

    assert report.examined == 2, "هر دو زائر باید در پنجرهٔ یادآور دیده شوند"
    assert report.sent == 1, "زائر سالم باید یادآور بگیرد"
    assert report.skipped, "زائر خراب باید در گزارش «رد‌شده» بیاید"
    assert len(MockSmsProvider.outbox) == sent_before + 1
    statuses = set(SmsDelivery.objects.values_list("status", flat=True))
    assert SmsStatus.FAILED in statuses and (
        SmsStatus.SENT in statuses or SmsStatus.SUPPRESSED in statuses
    )


# ─────────────────────── ۹) قطعی کش: آمادگی «افت‌کرده» ──────────────────────
def test_readiness_degrades_when_cache_is_down_but_liveness_stays_green(
    monkeypatch: pytest.MonkeyPatch, client: Any
) -> None:
    """کش که بخوابد، سامانه «افت‌کرده» است — ولی زنده و پاسخ‌گو می‌ماند."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("cache backend is unreachable")

    monkeypatch.setattr(cache, "set", _boom)
    readiness = client.get("/health/ready/")
    payload = readiness.json()
    # کش، منبع حقیقت نیست: سامانه پاسخ‌گو می‌ماند (۲۰۰) ولی گزارش «افت‌کرده»
    # می‌شود و نام همان وابستگی می‌آید تا پایش هشدار بدهد.
    assert readiness.status_code == 200
    assert payload["status"] == "degraded"
    assert payload["degraded"] == ["cache"]
    assert payload["checks"]["cache"]["status"] == "degraded"

    live = client.get("/health/live/")
    assert live.status_code == 200
    assert live.json()["status"] == "alive"

    # و صفحهٔ عمومی نباید به‌خاطر کشِ خواب ۵۰۰ بدهد.
    home = client.get("/")
    assert home.status_code == 200


# ───────────────── ۱۰) ورودی بدخیم: هرگز ۵۰۰، همیشه ۴xx/۲۰۰ ─────────────────
@pytest.mark.parametrize(
    ("method", "path", "payload", "content_type"),
    [
        ("post", "/login/", {"phone": "0" * 5000}, "application/json"),
        ("post", "/login/", {"phone": ["not", "a", "phone"]}, "application/json"),
        ("get", "/help/?q=" + "الف" * 5000, None, None),
        ("get", "/api/v1/meta?cursor=" + "x" * 4000, None, None),
        ("get", "/%%%broken-uri%%%", None, None),
    ],
)
def test_hostile_inputs_never_produce_a_server_error(
    client: Any, method: str, path: str, payload: Any, content_type: str | None
) -> None:
    """ورودی بزرگ/بدشکل/ناسازگار: پاسخ ۴xx یا ۲۰۰ — هرگز ۵۰۰."""
    if method == "post":
        response = client.post(path, data=payload, content_type=content_type or "application/json")
    else:
        response = client.get(path)
    assert response.status_code < 500, f"{method.upper()} {path[:60]} ⇒ {response.status_code}"


def test_oversized_body_is_refused_not_crashed(client: Any) -> None:
    """بدنهٔ ۲ مگابایتی نباید سرور را بترکاند."""
    response = client.post(
        "/api/v1/waitlist",
        data=b'{"note": "' + b"a" * 2_000_000 + b'"}',
        content_type="application/json",
    )
    assert response.status_code < 500
