#!/usr/bin/env python3
"""بارسنجی و آزمون سرعت مهدیه — بدون وابستگی سنگین، با گزارش قابل‌استناد.

چرا اسکریپت مستقل و نه ابزار بیرونی؟ چون می‌خواهیم بارسنجی «قرارداد پذیرش»
باشد: در CI، روی ماشین اپراتور، و پیش از انتشار اجرا شود و **گزارش ماندگار**
بسازد. یک ابزار گرافیکی خوش‌نما این را نمی‌دهد؛ اما دو صد خط پایتون با آستانهٔ
صریح، بله.

مدل بار: کاربرانِ هم‌زمان (thread) روی مسیرهای واقعیِ پرترافیک، با وزن‌دهی
شبیه‌سازی‌شدهٔ رفتار زائر:
    • صفحهٔ خانه و فهرست واحدها ⇒ کشف و تصمیم
    • جزئیات واحد و تقویم       ⇒ مقایسهٔ تاریخ‌ها (سنگین‌ترین مسیر)
    • سلامت‌سنجی                ⇒ بارِ پایشگر بیرونی (باید ارزان باشد)

معیار پذیرش (قابل تنظیم با فلگ‌ها):
    • خطای ۵xx صفر
    • نرخ خطای کل ≤ ۱٪
    • p95 کل ≤ ۸۰۰ میلی‌ثانیه روی میزبان توسعه

استفاده:
    scripts/load_test.py --base-url http://127.0.0.1:8010 --users 20 --seconds 20
    scripts/load_test.py --base-url https://mahdieh.ir --users 50 --seconds 60 --report docs/load/x.md
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import math
import pathlib
import random
import statistics
import sys
import threading
import time
from typing import Final

import requests

# ── سناریو: مسیر با وزن، انتظار وضعیت، و برچسب خوانا (فارسی در گزارش) ────────
SCENARIOS: Final[list[tuple[str, str, int, frozenset[int]]]] = [
    ("خانه", "/", 22, frozenset({200})),
    ("فهرست واحدها", "/units/", 18, frozenset({200})),
    ("تقویم ماهانه", "/calendar/", 12, frozenset({200})),
    ("جزئیات واحد", "/units/{unit}/", 16, frozenset({200, 404})),
    ("صفحهٔ ورود", "/login/", 8, frozenset({200})),
    ("واحدهای من (ناشناس⇒ورود)", "/my/", 6, frozenset({302})),
    ("اهداف پایش", "/health/ready/", 10, frozenset({200})),
    ("دارایی ایستا", "{static}", 8, frozenset({200})),
]

UNIT_CODES: Final[tuple[str, ...]] = ("A1", "B1", "A2", "B2", "B3")
DEFAULT_STATIC: Final[str] = "/static/web/css/mahdieh.css"


@dataclass
class Sample:
    """یک نمونه اندازه‌گیری: مسیر، وضعیت، تأخیر (میلی‌ثانیه)، خطا."""

    label: str
    status: int
    millis: float
    error: str = ""


@dataclass
class Collector:
    """گردآورندهٔ نمونه‌های همهٔ رشته‌ها با قفل (تجمیع سبک و بی‌رقابت)."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        """افزودن یک نمونه."""
        with self.lock:
            self.samples.append(sample)


def _resolve_static_path(session: requests.Session, base_url: str, cache: dict[str, str]) -> str:
    """مسیر واقعی دارایی ایستا را از صفحهٔ خانه می‌خواند (برای نسخهٔ هش‌شده)."""
    if "path" in cache:
        return cache["path"]
    try:
        response = session.get(f"{base_url}/", timeout=10)
        for token in response.text.split('"'):
            if "/static/" in token and token.endswith((".css", ".js")):
                cache["path"] = token
                return token
    except requests.RequestException:
        pass
    cache["path"] = DEFAULT_STATIC
    return cache["path"]


def _worker(
    base_url: str,
    headers: dict[str, str],
    stop_at: float,
    collector: Collector,
    static_cache: dict[str, str],
    seed: int,
    think_time: float,
) -> None:
    """یک کاربر مجازی: تا زمان پایان، مسیرها را به‌تصادفِ وزنی می‌زند."""
    session = requests.Session()
    session.headers.update(headers)
    static_path = _resolve_static_path(session, base_url, static_cache)
    rng = random.Random(seed)
    labels = [row[0] for row in SCENARIOS]
    weights = [row[2] for row in SCENARIOS]
    scenario_map = {row[0]: row for row in SCENARIOS}

    while time.monotonic() < stop_at:
        label = rng.choices(labels, weights=weights, k=1)[0]
        _, template, _, allowed = scenario_map[label]
        path = template.format(
            unit=rng.choice(UNIT_CODES), static=static_path.lstrip("/") or DEFAULT_STATIC
        )
        if path.startswith("/static/") or template == "{static}":
            path = static_path
        started = time.perf_counter()
        status = 0
        error = ""
        try:
            response = session.get(f"{base_url}{path}", timeout=15, allow_redirects=False)
            status = response.status_code
            if status not in allowed:
                error = f"وضعیت غیرمنتظره {status}"
        except requests.RequestException as exc:  # pragma: no cover - بستگی به شبکه دارد
            error = type(exc).__name__
        elapsed_ms = (time.perf_counter() - started) * 1000
        collector.add(Sample(label=label, status=status, millis=elapsed_ms, error=error))
        if think_time:
            time.sleep(think_time)


def _percentile(values: list[float], fraction: float) -> float:
    """صدک با درون‌یابی خطی (روش استاندارد numpy)."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[int(position)]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _fa(value: str) -> str:
    """تبدیل رقم‌های لاتین به فارسی برای گزارش."""
    table = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    return value.translate(table)


def run(args: argparse.Namespace) -> int:
    """اجرای بارسنجی و ساخت گزارش؛ کد خروج بر پایهٔ آستانه‌ها."""
    base_url = args.base_url.rstrip("/")
    headers: dict[str, str] = {}
    for raw in args.header:
        key, _, value = raw.partition(":")
        headers[key.strip()] = value.strip()

    collector = Collector()
    static_cache: dict[str, str] = {}

    # گرم‌کردن: نخستین درخواست باید کش/کارگر را آماده کند تا صدک‌ها واقعی باشند
    try:
        requests.get(f"{base_url}/health/ready/", headers=headers, timeout=10)
    except requests.RequestException as exc:
        print(f"❌ سامانه در دسترس نیست: {exc}", file=sys.stderr)
        return 2

    print(f"بارسنجی روی {base_url} — {args.users} کاربر، {args.seconds} ثانیه")
    started_wall = datetime.now(UTC)
    stop_at = time.monotonic() + args.seconds
    threads = [
        threading.Thread(
            target=_worker,
            args=(base_url, headers, stop_at, collector, static_cache, 1000 + index, args.think),
            daemon=True,
        )
        for index in range(args.users)
    ]
    for thread in threads:
        thread.start()
        time.sleep(args.ramp / max(1, args.users))
    for thread in threads:
        thread.join()

    elapsed = (datetime.now(UTC) - started_wall).total_seconds()
    samples = collector.samples
    if not samples:
        print("❌ هیچ نمونه‌ای ثبت نشد", file=sys.stderr)
        return 2

    latencies = sorted(sample.millis for sample in samples)
    failures = [sample for sample in samples if sample.error]
    server_errors = [sample for sample in samples if sample.status >= 500]
    by_label: dict[str, list[Sample]] = {}
    for sample in samples:
        by_label.setdefault(sample.label, []).append(sample)

    total = len(samples)
    error_rate = len(failures) / total
    rps = total / elapsed
    p50 = _percentile(latencies, 0.50)
    p90 = _percentile(latencies, 0.90)
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)

    lines: list[str] = []
    lines.append(f"# گزارش بارسنجی مهدیه — {started_wall:%Y-%m-%d}")
    lines.append("")
    lines.append("| سنجه | مقدار | آستانهٔ پذیرش |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| کاربران هم‌زمان | {_fa(str(args.users))} | — |")
    lines.append(f"| مدت اجرا | {_fa(f'{elapsed:.1f}')} ثانیه | — |")
    lines.append(f"| کل درخواست‌ها | {_fa(f'{total:,}')} | — |")
    lines.append(f"| توان عبوری (RPS) | {_fa(f'{rps:.1f}')} | — |")
    lines.append(
        f"| نرخ خطا | {_fa(f'{error_rate * 100:.2f}')}٪ | ≤ {_fa('1.00')}٪ |"
    )
    lines.append(f"| خطای ۵xx | {_fa(str(len(server_errors)))} | {_fa('0')} |")
    lines.append(f"| میانه (p50) | {_fa(f'{p50:.0f}')} میلی‌ثانیه | — |")
    lines.append(f"| p90 | {_fa(f'{p90:.0f}')} میلی‌ثانیه | — |")
    lines.append(
        f"| p95 | {_fa(f'{p95:.0f}')} میلی‌ثانیه | ≤ {_fa(str(args.max_p95))} میلی‌ثانیه |"
    )
    lines.append(f"| p99 | {_fa(f'{p99:.0f}')} میلی‌ثانیه | — |")
    lines.append(f"| میانگین | {_fa(f'{statistics.fmean(latencies):.0f}')} میلی‌ثانیه | — |")
    lines.append("")
    lines.append("## تفکیک مسیرها")
    lines.append("")
    lines.append("| مسیر | تعداد | میانه | p95 | خطا |")
    lines.append("| --- | --- | --- | --- | --- |")
    for label, rows in sorted(by_label.items(), key=lambda item: -len(item[1])):
        values = sorted(row.millis for row in rows)
        row_errors = sum(1 for row in rows if row.error)
        lines.append(
            f"| {label} | {_fa(f'{len(rows):,}')} | {_fa(f'{_percentile(values, 0.5):.0f}')} "
            f"| {_fa(f'{_percentile(values, 0.95):.0f}')} | {_fa(str(row_errors))} |"
        )
    lines.append("")
    lines.append("## کدهای وضعیت")
    lines.append("")
    lines.append("| وضعیت | تعداد |")
    lines.append("| --- | --- |")
    for status, count in Counter(sample.status for sample in samples).most_common():
        lines.append(f"| {_fa(str(status))} | {_fa(f'{count:,}')} |")
    lines.append("")
    lines.append(
        f"> تولید‌شده با `scripts/load_test.py` — {_fa(started_wall.isoformat(timespec='seconds'))}"
    )
    report_text = "\n".join(lines) + "\n"

    print(report_text)

    if args.report:
        path = pathlib.Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_text, encoding="utf-8")
        print(f"گزارش در {path} ذخیره شد.")
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(
                {
                    "users": args.users,
                    "seconds": elapsed,
                    "requests": total,
                    "rps": rps,
                    "error_rate": error_rate,
                    "server_errors": len(server_errors),
                    "p50_ms": p50,
                    "p90_ms": p90,
                    "p95_ms": p95,
                    "p99_ms": p99,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    verdict = []
    if server_errors:
        verdict.append(f"خطای ۵xx: {len(server_errors)}")
    if error_rate > args.max_error_rate:
        verdict.append(f"نرخ خطا {error_rate * 100:.2f}٪ بیش از آستانه")
    if p95 > args.max_p95:
        verdict.append(f"p95 {p95:.0f}ms بیش از آستانه {args.max_p95}ms")
    if verdict:
        print("❌ بارسنجی رد شد: " + " | ".join(verdict), file=sys.stderr)
        return 1
    print("✅ بارسنجی پذیرفته شد.")
    return 0


def main() -> int:
    """نقطهٔ ورود خط فرمان."""
    parser = argparse.ArgumentParser(description="بارسنجی سامانهٔ رزرو مهدیه")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=10, help="کاربران هم‌زمان")
    parser.add_argument("--seconds", type=int, default=15, help="مدت هر کاربر")
    parser.add_argument("--ramp", type=float, default=1.0, help="مدت پله‌ای‌شدن شروع (ثانیه)")
    parser.add_argument("--think", type=float, default=0.0, help="مکث بین درخواست‌ها")
    parser.add_argument("--header", action="append", default=[], help="هدر اضافه (Key: Value)")
    parser.add_argument("--max-p95", type=float, default=800.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--report", default="", help="مسیر گزارش Markdown")
    parser.add_argument("--json", default="", help="مسیر خروجی JSON برای پایش")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
