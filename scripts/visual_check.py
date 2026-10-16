#!/usr/bin/env python
"""بازرسی چشمی خودکار — سایت را در مرورگر واقعی می‌گیرد و تصویر می‌سازد.

چرا لازم است؟ صفحه‌ای که کد ۲۰۰ برمی‌گرداند می‌تواند از نظر چیدمان شکسته باشد
(فونت بارگذاری‌نشده، ستون‌های به‌هم‌ریخته، دکمهٔ نامرئی). این ابزار همان چیزی را
می‌بیند که زائر می‌بیند.

پیش‌نیاز:
    uv pip install playwright && python -m playwright install chromium
    uv run python src/manage.py runserver 0.0.0.0:8000   # در پایهٔ جدا

اجرا:
    uv run python scripts/visual_check.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

DESKTOP = {"width": 1440, "height": 1000}
MOBILE = {"width": 400, "height": 900}
HTTP_BAD = 400  # از این کد به بالا یعنی صفحه خطا برگردانده است

# مسیرهایی که سایت باید بی‌ایراد نشان دهد. برای هر مسیر مشخص می‌کنیم با کدام
# نقش (مهمان/کارمند) دیده شود؛ نقش‌ها از مسیر نمایشی `/demo/<role>/` می‌آیند.
PAGES: list[tuple[str, str, str]] = [
    ("home", "guest", "/"),
    ("units", "guest", "/units/"),
    ("unit-detail", "guest", "/units/A2/"),
    ("calendar", "guest", "/calendar/"),
    ("login", "anon", "/login/"),
    ("my-reservations", "guest", "/my/"),
    ("profile", "guest", "/profile/"),
    ("book", "guest", "/book/A1/"),
    ("board", "staff", "/board/"),
    ("robots", "anon", "/robots.txt"),
]

# نشانه‌های شکست که هرگز نباید در صفحهٔ نهایی دیده شوند.
BAD_MARKERS = ["Traceback", "Server Error (500)", "TemplateSyntaxError", "DisallowedHost"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="بازرسی چشمی خودکار سایت مهدیه")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="/home/user/_qa")
    parser.add_argument("--mobile", action="store_true", help="فقط نمای موبایل")
    return parser


def main() -> int:
    """اجرای بازرسی؛ ۰ یعنی همهٔ صفحه‌ها سالم بودند."""
    args = _parser().parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright نصب نیست: uv pip install playwright && python -m playwright install chromium"
        )
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    viewports = [("mobile", MOBILE)] if args.mobile else [("desktop", DESKTOP), ("mobile", MOBILE)]
    failures: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for label, viewport in viewports:
            contexts: dict[str, object] = {}
            for role in ("anon", "guest", "staff"):
                context = browser.new_context(
                    viewport=viewport, locale="fa-IR", device_scale_factor=1
                )
                page = context.new_page()
                if role != "anon":
                    response = page.goto(f"{args.base_url}/demo/{role}/", wait_until="networkidle")
                    if response is None or response.status >= HTTP_BAD:
                        failures.append(
                            f"{label}/{role}: ورود نمایشی با کد {response and response.status}"
                        )
                contexts[role] = (context, page)

            for name, role, path in PAGES:
                context, page = contexts[role]  # type: ignore[misc]
                response = page.goto(f"{args.base_url}{path}", wait_until="networkidle")
                status = response.status if response else 0
                body = page.content()
                bad = [marker for marker in BAD_MARKERS if marker in body]
                shot = out / f"{label}-{name}.png"
                page.screenshot(path=str(shot), full_page=True)
                flag = "✅"
                if status >= HTTP_BAD or bad:
                    flag = "❌"
                    failures.append(f"{label}/{name} ({path}): کد {status} {bad}")
                print(f"{flag} {label:8s} {name:16s} {path:26s} {status} → {shot.name}")

            for context, _page in contexts.values():  # type: ignore[misc]
                context.close()  # type: ignore[attr-defined]
        browser.close()

    print()
    if failures:
        print(f"❌ {len(failures)} ایراد:")
        for item in failures:
            print("   -", item)
        return 1
    print("✅ همهٔ صفحه‌ها سالم و بدون خطا رندر شدند.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
