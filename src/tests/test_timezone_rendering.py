"""تست نمایش زمان — «ساعت محلی» یا «UTC»؟ این تست جلوی خطای خاموش را می‌گیرد.

قاعده: مدل‌ها UTC ذخیره می‌کنند (``USE_TZ=True``) و کاربر تهران است؛ هر جایی که
زمان به کاربر نشان داده می‌شود باید از منطقهٔ محلی بگذرد. این خطای واقعی در
بازرسی چشمی پیدا شد (ساعت ۰۴:۳۰ به‌جای ۰۸:۰۰) و این‌جا قفل می‌شود.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone as dt_timezone
import re

from django.urls import reverse
from django.utils import timezone
import pytest

from apps.web.presenters import moment_label
from apps.web.templatetags.mahdieh import clock, jalali
from tests.factories import days_ahead

pytestmark = pytest.mark.django_db

TEHRAN = dt_timezone(timedelta(hours=3, minutes=30))


def test_clock_converts_utc_to_tehran() -> None:
    """۰۴:۳۰ به وقت جهانی باید ۰۸:۰۰ تهران دیده شود، نه ۰۴:۳۰."""
    assert clock(datetime(2026, 10, 19, 4, 30, tzinfo=UTC)) == "۰۸:۰۰"


def test_clock_keeps_plain_time_untouched() -> None:
    """ساعت ساده (``time``) — مثل «ساعت تقریبی ورود» — تبدیل نمی‌شود."""
    assert clock(time(14, 5)) == "۱۴:۰۵"


def test_jalali_uses_local_day_not_utc_day() -> None:
    """۲۱:۰۰ UTC همان ۰۰:۳۰ روز بعد در تهران است؛ تاریخ باید روزِ محلی باشد."""
    value = datetime(2026, 10, 19, 21, 0, tzinfo=UTC)
    assert jalali(value) == jalali(value.astimezone(TEHRAN))
    assert "۲۸" in jalali(value)


def test_moment_label_is_local() -> None:
    """برچسب لحظه (استفاده در گواهی/تابلو) هم به وقت محلی است."""
    value = datetime(2026, 10, 19, 4, 30, tzinfo=UTC)
    label = moment_label(value)
    assert "۰۸:۰۰" in label or "08:00" in label
    assert "۰۴:۳۰" not in label


def test_filters_never_raise_on_garbage() -> None:
    """ورودی نبودِ مقدار یا رشتهٔ بی‌ربط، صفحه را نمی‌شکند."""
    assert clock(None) == "—"
    assert jalali(None) == "—"
    assert jalali("1405/07/25") == "—"


def test_reservation_card_shows_local_session_hours(client, guest, units) -> None:
    """کارت رزرو سانس روز باید ۰۸:۰۰ تا ۲۰:۰۰ نشان دهد — نه ساعت UTC."""
    client.force_login(guest)
    response = client.post(
        reverse("web:book", args=["A1"]),
        {
            "unit_code": "A1",
            "day": days_ahead(3).isoformat(),
            "period": "day",
            "cells": "2",
            "guests_count": "2",
            "idempotency_key": "tz-test-key-1",
        },
        follow=True,
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "۰۸:۰۰" in body, "ساعت شروع باید به وقت تهران باشد"
    assert "۰۴:۳۰" not in body, "ساعت UTC نباید در صفحهٔ کاربر دیده شود"
    # دو نیم‌روز از سانس روز = روز + شب ⇒ پایان ۰۸:۰۰ روز بعد. تاریخ از تقویم
    # محاسبه می‌شود (نه رشتهٔ ثابت) تا آزمون به روز اجرا وابسته نباشد.
    assert jalali(days_ahead(4)) in body, "پایان اقامت باید ۰۸:۰۰ روز بعد باشد"


def test_board_last_refresh_uses_local_time(client, operator) -> None:
    """نشانگر «آخرین به‌روزرسانی» تابلوی کارکنان باید ساعت تهران باشد."""
    client.force_login(operator)
    response = client.get(reverse("web:board"))
    assert response.status_code == 200
    body = response.content.decode()
    match = re.search(r"آخرین به‌روزرسانی: (\d{2}):(\d{2})", body)
    assert match, "نشانگر به‌روزرسانی در تابلوی کارکنان پیدا نشد"
    shown = int(match.group(1)) * 60 + int(match.group(2))  # ارقام فارسی هم پذیرفته‌اند
    local = timezone.localtime()
    expected = local.hour * 60 + local.minute
    assert abs(shown - expected) <= 1, "ساعت تابلو با وقت محلی نمی‌خواند"
