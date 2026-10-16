"""زبان بصری گزارش — رنگ، فونت، ستون و سبک‌های مشترک.

چرا جدا؟ چون «ظاهر» باید یک‌جا تعریف شود تا همهٔ برگه‌ها یکدست باشند و تغییر
هویت بصری، یک نقطهٔ تغییر داشته باشد — همان اصلی که در CSS سایت هم داریم.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── پالت مهدیه (هم‌رنگ با سایت) ───────────────────────────────────────────────
BRAND = "12604F"
BRAND_DARK = "0B3B31"
ACCENT = "C9A227"
SUCCESS = "166534"
WARNING = "B45309"
DANGER = "B91C1C"
INFO = "1D4ED8"
TEXT = "111827"
MUTED = "6B7280"
SURFACE = "F4F7F6"
ZEBRA = "FAFCFB"
BORDER = "D7E0DC"

# ── فونت ──────────────────────────────────────────────────────────────────────
FONT_FAMILY = "Vazirmatn"
FONT_FALLBACK = "Tahoma"

# ── شناسهٔ سند ────────────────────────────────────────────────────────────────
DOCUMENT_TITLE = "گزارش جامع رزرو زائرسرای مهدیه"
DOCUMENT_SUBTITLE = "وضعیت رزروها، سانس‌ها، اشغال واحدها و کنش‌های کارکنان"
DOCUMENT_FOOTER = "سامانهٔ رزرو زائرسرای مهدیه — مشهد · خروجی رسمی و قابل‌ممیزی"
DOCUMENT_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class Column:
    """تعریف یک ستون گزارش: عنوان، کلید داده، عرض و قالب نمایش."""

    header: str
    key: str
    width: float = 18.0
    number_format: str = ""
    align: str = "right"


def status_color(status: str) -> str:
    """رنگ متن بر پایهٔ وضعیت رزرو — نگاه یک‌نگاهی به سلامت سانس‌ها."""
    return {
        "confirmed": SUCCESS,
        "hold": WARNING,
        "checked_in": INFO,
        "completed": BRAND,
        "cancelled_by_guest": DANGER,
        "cancelled_by_staff": DANGER,
        "no_show": DANGER,
        "expired": MUTED,
        "superseded": MUTED,
    }.get(status, TEXT)


def occupancy_color(rate: float) -> str:
    """رنگ بر پایهٔ نرخ اشغال — سبز آرام، نارنجی پرکار، قرمز اشباع."""
    if rate >= 0.9:  # noqa: PLR2004 - مرزهای هشدار عمدی و مستند
        return DANGER
    if rate >= 0.7:  # noqa: PLR2004
        return WARNING
    if rate >= 0.4:  # noqa: PLR2004
        return ACCENT
    return SUCCESS
