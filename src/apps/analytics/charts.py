"""نمودارهای SVG بدون هیچ کتابخانهٔ بیرونی.

چرا SVG دست‌ساز؟ دو دلیل سخت‌گیرانه:
    ۱) CSP سایت هر اسکریپت و CDN بیرونی را می‌بندد؛ کتابخانهٔ نمودار ممکن نیست.
    ۲) خروجی باید در چاپ و پریview آفلاین هم دیده شود — SVG برداری و سبک است.

هر نمودار یک رشتهٔ SVG کامل و **قطعی** برمی‌گرداند (خروجی تابع ورودی، بدون
زمان یا تصادف) تا در تست و گزارش اکسل هم قابل استفاده باشد.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from typing import Any, Final
from urllib.parse import quote

from apps.core.text import to_persian_digits

#: آستانه‌های رنگ نقشهٔ حرارتی (سهم اشغال ⇒ رنگ) — یک‌جا و مستند.
HEAT_THRESHOLDS: Final[tuple[tuple[float, str], ...]] = (
    (0.85, "#a83b2c"),
    (0.65, "#c96a3f"),
    (0.35, "#d9a441"),
    (0.02, "#2f6f6a"),
)

#: آستانه‌های رنگ عقربهٔ نرخ اشغال: آرام / پرمشغله / اشباع.
GAUGE_BUSY: Final[float] = 0.7
GAUGE_SATURATED: Final[float] = 0.9

PALETTE: Final[dict[str, str]] = {
    "gold": "#d9a441",
    "gold_soft": "rgba(217, 164, 65, .18)",
    "teal": "#2fb3a3",
    "teal_soft": "rgba(47, 179, 163, .18)",
    "line": "rgba(255, 255, 255, .12)",
    "text": "#c9d3e4",
    "muted": "#7d8ba6",
    "danger": "#e0715f",
}


def fa_number(value: Any) -> str:
    """ارقام فارسی برای متن‌های SVG.

    نمودارها SVG خالص‌اند و از فیلترهای قالب رد نمی‌شوند؛ پس قاعدهٔ «هیچ عدد
    لاتینی در خروجی» باید همین‌جا هم رعایت شود، وگرنه داشبورد فارسی، عدد انگلیسی
    نشان می‌دهد. جدول ارقام تنها در ``apps.core.text`` است.
    """
    return to_persian_digits(value)


def sparkline(
    values: Sequence[float],
    *,
    width: int = 560,
    height: int = 120,
    stroke: str = PALETTE["teal"],
    fill: str = PALETTE["teal_soft"],
    label: str = "",
) -> str:
    """نمودار خطی کوچک برای روند اشغال (بدون محور، برای نوار خلاصه)."""
    if not values:
        return _svg(width, height, "")
    step = width / max(1, len(values) - 1)
    points = [
        (index * step, height - max(0.0, min(1.0, value)) * (height - 12) - 6)
        for index, value in enumerate(values)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = f"0,{height} " + line + f" {width},{height}"
    body = (
        f'<polygon points="{area}" fill="{fill}" />'
        f'<polyline points="{line}" fill="none" stroke="{stroke}" '
        f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />'
    )
    return _svg(width, height, body, label=label)


def bars(
    rows: Sequence[tuple[str, float]],
    *,
    width: int = 640,
    height: int = 220,
    label: str = "",
    accent: str = PALETTE["gold"],
) -> str:
    """نمودار ستونی افقی‌خوان (RTL) با برچسب و مقدار."""
    if not rows:
        return _svg(width, height, "")
    gap = 10
    bar_height = max(12, (height - gap * (len(rows) + 1)) / len(rows))
    parts: list[str] = []
    for index, (name, value) in enumerate(rows):
        y = gap + index * (bar_height + gap)
        ratio = max(0.0, min(1.0, value))
        parts.append(
            f'<text x="{width - 8}" y="{y + bar_height * 0.72:.1f}" text-anchor="end" '
            f'font-size="12" fill="{PALETTE["text"]}">{escape(str(name))}</text>'
        )
        track_x = 8
        track_w = width - 150
        parts.append(
            f'<rect x="{track_x}" y="{y:.1f}" width="{track_w}" height="{bar_height:.1f}" '
            f'rx="6" fill="rgba(255,255,255,.05)" />'
        )
        parts.append(
            f'<rect x="{track_x + track_w * (1 - ratio):.1f}" y="{y:.1f}" '
            f'width="{track_w * ratio:.1f}" height="{bar_height:.1f}" rx="6" fill="{accent}" />'
        )
        parts.append(
            f'<text x="{track_x + track_w + 6}" y="{y + bar_height * 0.72:.1f}" '
            f'font-size="12" fill="{PALETTE["muted"]}">{fa_number(f"{ratio * 100:.0f}")}٪</text>'
        )
    return _svg(width, height, "".join(parts), label=label)


def line_chart(
    points: Sequence[tuple[str, float]],
    *,
    width: int = 760,
    height: int = 240,
    stroke: str = PALETTE["teal"],
    fill: str = PALETTE["teal_soft"],
    label: str = "",
) -> str:
    """نمودار خطی با محور و برچسب‌های انتخابی (برای روند اشغال روزانه)."""
    if not points:
        return _svg(width, height, "")
    padding_x, padding_y = 36, 24
    inner_w = width - padding_x * 2
    inner_h = height - padding_y * 2
    step = inner_w / max(1, len(points) - 1)
    coords = [
        (
            padding_x + index * step,
            padding_y + inner_h - max(0.0, min(1.0, value)) * inner_h,
        )
        for index, (_label, value) in enumerate(points)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = (
        f"{padding_x},{padding_y + inner_h} "
        + line
        + f" {padding_x + inner_w},{padding_y + inner_h}"
    )
    parts = [
        f'<line x1="{padding_x}" y1="{padding_y + inner_h}" x2="{padding_x + inner_w}" '
        f'y2="{padding_y + inner_h}" stroke="{PALETTE["line"]}" stroke-width="1" />',
        f'<polygon points="{area}" fill="{fill}" />',
        f'<polyline points="{line}" fill="none" stroke="{stroke}" stroke-width="2.5" />',
    ]
    for index, (point_label, _value) in enumerate(points):
        if index % max(1, len(points) // 8) and index != len(points) - 1:
            continue
        x = padding_x + index * step
        parts.append(
            f'<text x="{x:.1f}" y="{height - 6}" text-anchor="middle" font-size="10" '
            f'fill="{PALETTE["muted"]}">{escape(point_label)}</text>'
        )
    parts.append(
        f'<text x="{padding_x}" y="14" font-size="11" fill="{PALETTE["muted"]}">'
        f"{fa_number(f'{points[-1][1] * 100:.0f}')}٪ آخرین نرخ</text>"
    )
    return _svg(width, height, "".join(parts), label=label)


def heatmap(
    rows: Sequence[tuple[str, Sequence[float]]],
    *,
    columns: int,
    width: int = 760,
    label: str = "",
    column_labels: Sequence[str] = (),
) -> str:
    """نقشهٔ حرارتی واحد × روز — «کجا و چه روزی پر است؟»."""
    if not rows or columns < 1:
        return _svg(width, 60, "")
    label_w = 96
    cell_w = max(6.0, (width - label_w - 12) / columns)
    cell_h = 22.0
    height = int(len(rows) * (cell_h + 4) + 34)
    parts: list[str] = []
    for index, row_label in enumerate(column_labels[:columns]):
        parts.append(
            f'<text x="{label_w + index * cell_w + cell_w / 2:.1f}" y="12" text-anchor="middle" '
            f'font-size="9" fill="{PALETTE["muted"]}">{escape(str(row_label))}</text>'
        )
    for row_index, (name, values) in enumerate(rows):
        y = 20 + row_index * (cell_h + 4)
        parts.append(
            f'<text x="{width - 8}" y="{y + cell_h * 0.7:.1f}" text-anchor="end" font-size="11" '
            f'fill="{PALETTE["text"]}">{escape(str(name))}</text>'
        )
        for col_index, value in enumerate(values[:columns]):
            ratio = max(0.0, min(1.0, value))
            color = _heat_color(ratio)
            x = label_w + col_index * cell_w
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w - 1.5:.1f}" '
                f'height="{cell_h:.1f}" rx="3" fill="{color}"><title>{escape(str(name))} · '
                f"{fa_number(f'{ratio * 100:.0f}')}٪</title></rect>"
            )
    return _svg(width, height, "".join(parts), label=label)


def _heat_color(ratio: float) -> str:
    """رنگ سلول نقشهٔ حرارتی بر پایهٔ سهم اشغال (از سرد به گرم)."""
    for threshold, color in HEAT_THRESHOLDS:
        if ratio >= threshold:
            return color
    return PALETTE["teal_soft"]


def donut(
    segments: Sequence[tuple[str, float, str]],
    *,
    size: int = 180,
    label: str = "",
) -> str:
    """نمودار دونات (سهم وضعیت‌ها) با SVG خالص و برچسب درصد."""
    total = sum(value for _name, value, _color in segments) or 1.0
    radius = size / 2 - 14
    circumference = 2 * 3.141592653589793 * radius
    offset = 0.0
    parts: list[str] = [
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{radius}" fill="none" '
        f'stroke="rgba(255,255,255,.07)" stroke-width="16" />'
    ]
    for _name, value, color in segments:
        fraction = max(0.0, value) / total
        dash = circumference * fraction
        parts.append(
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{radius}" fill="none" stroke="{color}" '
            f'stroke-width="16" stroke-dasharray="{dash:.2f} {circumference - dash:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {size / 2} {size / 2})" '
            f'stroke-linecap="butt" />'
        )
        offset += dash
    parts.append(
        f'<text x="{size / 2}" y="{size / 2 + 4}" text-anchor="middle" font-size="18" '
        f'fill="{PALETTE["text"]}">{fa_number(f"{total:.0f}")}</text>'
    )
    return _svg(size, size, "".join(parts), label=label)


def gauge(value: float, *, size: int = 150, label: str = "") -> str:
    """نمای کمانی یک نرخ (۰ تا ۱) با درصد درشت."""
    ratio = max(0.0, min(1.0, value))
    radius = size / 2 - 12
    circumference = 3.141592653589793 * radius
    dash = circumference * ratio
    color = (
        PALETTE["teal"]
        if ratio < GAUGE_BUSY
        else PALETTE["gold"]
        if ratio < GAUGE_SATURATED
        else PALETTE["danger"]
    )
    body = (
        f'<path d="M 12 {size - 24} A {radius} {radius} 0 0 1 {size - 12} {size - 24}" '
        f'fill="none" stroke="rgba(255,255,255,.08)" stroke-width="14" />'
        f'<path d="M 12 {size - 24} A {radius} {radius} 0 0 1 {size - 12} {size - 24}" '
        f'fill="none" stroke="{color}" stroke-width="14" stroke-dasharray="{dash:.2f} '
        f'{circumference - dash:.2f}" />'
        f'<text x="{size / 2}" y="{size - 34}" text-anchor="middle" font-size="22" '
        f'fill="{PALETTE["text"]}">{fa_number(f"{ratio * 100:.0f}")}٪</text>'
    )
    return _svg(size, size, body, label=label)


def data_uri(svg: str) -> str:
    """تبدیل SVG به data-URI برای درج در اکسل یا ایمیل (بدون فایل کنار)."""
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


def _svg(width: int, height: int, body: str, *, label: str = "") -> str:
    """پوستهٔ مشترک همهٔ نمودارها — با نقش دسترس‌پذیری."""
    aria = escape(label) if label else "نمودار"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="auto" role="img" aria-label="{aria}" '
        f'preserveAspectRatio="xMidYMid meet">{body}</svg>'
    )
