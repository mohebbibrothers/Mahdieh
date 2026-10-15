"""تست پوشاندن دادهٔ شخصی در لاگ — قاعدهٔ «PII هرگز در لاگ نمی‌رود»."""

from __future__ import annotations

import pytest

from apps.core.logging import scrub_text


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "تماس با 09121234567 برای هماهنگی",
        "شماره: +989121234567 ثبت شد",
        "کد ملی کاربر 1234567890 تأیید شد",
        "شمارهٔ تماس 00989121112233",
    ],
)
def test_phone_and_national_id_are_masked(raw: str) -> None:
    """تلفن و کد ملی در متن لاگ پوشانده می‌شوند."""
    cleaned = scrub_text(raw)
    assert "[phone]" in cleaned or "[national-id]" in cleaned
    assert "09121234567" not in cleaned
    assert "1234567890" not in cleaned


@pytest.mark.unit
def test_secrets_are_redacted() -> None:
    """کلید و توکن در متن لاگ پوشانده می‌شوند."""
    cleaned = scrub_text("authorization: Bearer abcdef123456")
    assert "abcdef123456" not in cleaned
    assert "[redacted]" in cleaned


@pytest.mark.unit
def test_normal_text_is_untouched() -> None:
    """متن سالم نباید دست‌کاری شود."""
    raw = "رزرو سوئیت طبقهٔ اول برای پنج‌شنبه ثبت شد."
    assert scrub_text(raw) == raw
