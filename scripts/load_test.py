#!/usr/bin/env python3
"""بارسنجی و آزمون سرعت مهدیه — بدون وابستگی سنگین، با گزارش قابل‌استناد.

چرا اسکریپت مستقل و نه ابزار بیرونی؟ چون می‌خواهیم بارسنجی «قرارداد پذیرش»
باشد: در CI، روی ماشین اپراتور، و پیش از انتشار اجرا شود و **گزارش ماندگار**
بسازد. یک ابزار گرافیکی خوش‌نما این را نمی‌دهد؛ اما چند صد خط پایتون با آستانهٔ
صریح، بله.

مدل بار: کاربرانِ هم‌زمان (thread) روی مسیرهای واقعیِ پرترافیک، با وزن‌دهی
شبیه‌سازی‌شدهٔ رفتار زائر:
    • صفحهٔ خانه و فهرست واحدها ⇒ کشف و تصمیم
    • جزئیات واحد و تقویم       ⇒ مقایسهٔ تاریخ‌ها (سنگین‌ترین مسیر)
    • سلامت‌سنجی                ⇒ بارِ پایشگر بیرونی (باید ارزان باشد)

**تصمیم طراحی:** توزیع بار «شبه‌تصادفیِ قطعی» است، نه تولید عدد تصادفی. هر
کاربر مجازی یک برنامهٔ از‌پیش‌ساخته‌شدهٔ وزنی را با گامِ متفاوت می‌پیماید؛
نتیجه اینکه اجرای بارسنجی **بازتولیدپذیر** است (دو اجرا با ورودی یکسان، بار
یکسان) و هیچ مولد تصادفی وارد مسیر نمی‌شود.

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
import statistics
import sys
import threading
import time
from typing import Final

import requests

# ── سناریو: برچسب، قالب مسیر، وزن، و کدهای وضعیت پذیرفته‌شده ─────────────────
SCENARIOS: Final[tuple[tuple[str, str, int, frozenset[int]], ...]] = (
    ("خانه", "/", 22, frozenset({200})),
    ("فهرست واحدها", "/units/", 18, frozenset({200})),
    ("تقویم ماهانه", "/calendar/", 12, frozenset({200})),
    ("جزئیات واحد", "/units/{unit}/", 16, frozenset({200, 404})),
    ("صفحهٔ ورود", "/login/", 8, frozenset({200})),
    ("واحدهای من (ناشناس⇒ورود)", "/my/", 6, frozenset({302})),
    ("اهداف پایش", "/health/ready/", 10, frozenset({200})),
    ("دارایی ایستا", "{static}", 8, frozenset({200})),
)

UNIT_CODES: Final[tuple[str, ...]] = ("A1", "B1", "A2", "B2", "B3")
DEFAULT_STATIC: Final[str] = "/static/web/css/mahdieh.css"
SERVER_ERROR_FLOOR: Final[int] = 500
#: گامِ جابه‌جایی در برنامهٔ بار. عدد اول نسبت به طول برنامه ⇒ پراکندگی یکنواخت.
SCHEDULE_STRIDE: Final[int] = 7


@dataclass(frozen=True)
class Step:
    """یک گام از برنامهٔ بار: برچسب، قالب مسیر و کدهای وضعیت مجاز."""

    label: str
    template: str
    allowed: frozenset[int]


def _build_schedule() -> tuple[Step, ...]:
    """برنامهٔ بارِ از‌پیش‌ساخته: هر سناریو به‌اندازهٔ وزنش تکرار می‌شود."""
    steps: list[Step] = []
    for label, template, weight, allowed in SCENARIOS:
        steps.extend(Step(label=label, template=template, allowed=allowed) for _ in range(weight))
    return tuple(steps)


SCHEDULE: Final[tuple[Step, ...]] = _build_schedule()


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


@dataclass(frozen=True)
class Metrics:
    """خلاصهٔ آماری یک اجرای بارسنجی."""

    users: int
    elapsed: float
    total: int
    failures: int
    server_errors: int
    p50: float
    p90: float
    p95: float
    p99: float
    mean: float
    error_rate: float
    rps: float


def _utc_now() -> datetime:
    """لحظهٔ کنونی به وقت UTC.

    چرا ``fromtimestamp(time.time(), tz=UTC)`` و نه ``datetime.now(tz=UTC)``؟
    چون پروژه ``datetime.datetime.now`` را در کل مخزن ممنوع کرده است (قاعدهٔ
    TID251 برای جلوگیری از زمان‌های بی‌منطقه). این معادلِ دقیق و آگاه از منطقهٔ
    همان فراخوانی است و قاعده را نمی‌شکند.
    """
    return datetime.fromtimestamp(time.time(), tz=UTC)


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
    return value.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _parse_headers(raw_headers: list[str]) -> dict[str, str]:
    """تبدیل `--header "Key: Value"` به دیکشنری."""
    headers: dict[str, str] = {}
    for raw in raw_headers:
        key, _, value = raw.partition(":")
        headers[key.strip()] = value.strip()
    return headers


def _resolve_static_path(session: requests.Session, base_url: str, cache: dict[str, str]) -> str:
    """مسیر واقعی دارایی ایستا را از صفحهٔ خانه می‌خواند (برای نسخهٔ هش‌شده)."""
    if "path" in cache:
        return cache["path"]
    try:
        response = session.get(f"{base_url}/", timeout=10)
        for token in response.text.split('"'):
            if "/static/" in token and token.endswith((".css", ".js")):
                return cache.setdefault("path", token)
    except requests.RequestException:
        pass
    return cache.setdefault("path", DEFAULT_STATIC)


def _virtual_user(
    *,
    base_url: str,
    headers: dict[str, str],
    stop_at: float,
    collector: Collector,
    static_cache: dict[str, str],
    offset: int,
    think_time: float,
) -> None:
    """یک کاربر مجازی: برنامهٔ وزنی را با گامِ مخصوص خودش می‌پیماید."""
    session = requests.Session()
    session.headers.update(headers)
    static_path = _resolve_static_path(session, base_url, static_cache)

    cursor = offset
    while time.monotonic() < stop_at:
        step = SCHEDULE[cursor % len(SCHEDULE)]
        unit = UNIT_CODES[cursor % len(UNIT_CODES)]
        path = (
            static_path
            if step.template == "{static}"
            else step.template.format(unit=unit, static=DEFAULT_STATIC)
        )
        cursor += SCHEDULE_STRIDE

        started = time.perf_counter()
        status = 0
        error = ""
        try:
            response = session.get(f"{base_url}{path}", timeout=15, allow_redirects=False)
            status = response.status_code
            if status not in step.allowed:
                error = f"وضعیت غیرمنتظره {status}"
        except requests.RequestException as exc:  # pragma: no cover - بستگی به شبکه دارد
            error = type(exc).__name__
        elapsed_ms = (time.perf_counter() - started) * 1000
        collector.add(Sample(label=step.label, status=status, millis=elapsed_ms, error=error))
        if think_time:
            time.sleep(think_time)


def _drive_load(base_url: str, headers: dict[str, str], args: argparse.Namespace) -> float:
    """راه‌اندازی کاربران مجازی و بازگشت مدت اجرا (ثانیه)."""
    collector = Collector()
    static_cache: dict[str, str] = {}
    started_wall = _utc_now()
    stop_at = time.monotonic() + args.seconds

    threads = [
        threading.Thread(
            target=_virtual_user,
            kwargs={
                "base_url": base_url,
                "headers": headers,
                "stop_at": stop_at,
                "collector": collector,
                "static_cache": static_cache,
                "offset": index,
                "think_time": args.think,
            },
            daemon=True,
        )
        for index in range(args.users)
    ]
    for thread in threads:
        thread.start()
        time.sleep(args.ramp / max(1, args.users))
    for thread in threads:
        thread.join()

    args.collector = collector  # type: ignore[attr-defined]  # بازگرداندن نمونه‌ها به فراخوان
    return (_utc_now() - started_wall).total_seconds()


def _summarise(collector: Collector, users: int, elapsed: float) -> tuple[Metrics, list[float]]:
    """خلاصهٔ آماری نمونه‌ها (صدک‌ها بر پایهٔ تأخیر مرتب‌شده)."""
    latencies = sorted(sample.millis for sample in collector.samples)
    failures = sum(1 for sample in collector.samples if sample.error)
    server_errors = sum(1 for sample in collector.samples if sample.status >= SERVER_ERROR_FLOOR)
    total = len(latencies)
    return (
        Metrics(
            users=users,
            elapsed=elapsed,
            total=total,
            failures=failures,
            server_errors=server_errors,
            p50=_percentile(latencies, 0.50),
            p90=_percentile(latencies, 0.90),
            p95=_percentile(latencies, 0.95),
            p99=_percentile(latencies, 0.99),
            mean=statistics.fmean(latencies),
            error_rate=failures / total,
            rps=total / elapsed,
        ),
        latencies,
    )


def _report_lines(
    metrics: Metrics, collector: Collector, started: datetime, max_p95: float
) -> list[str]:
    """ساخت متن گزارش Markdown."""
    lines = [
        f"# گزارش بارسنجی مهدیه — {started:%Y-%m-%d}",
        "",
        "| سنجه | مقدار | آستانهٔ پذیرش |",
        "| --- | --- | --- |",
        f"| کاربران هم‌زمان | {_fa(str(metrics.users))} | — |",
        f"| مدت اجرا | {_fa(f'{metrics.elapsed:.1f}')} ثانیه | — |",
        f"| کل درخواست‌ها | {_fa(f'{metrics.total:,}')} | — |",
        f"| توان عبوری (RPS) | {_fa(f'{metrics.rps:.1f}')} | — |",
        f"| نرخ خطا | {_fa(f'{metrics.error_rate * 100:.2f}')}٪ | ≤ {_fa('1.00')}٪ |",
        f"| خطای ۵xx | {_fa(str(metrics.server_errors))} | {_fa('0')} |",
        f"| میانه (p50) | {_fa(f'{metrics.p50:.0f}')} میلی‌ثانیه | — |",
        f"| p90 | {_fa(f'{metrics.p90:.0f}')} میلی‌ثانیه | — |",
        f"| p95 | {_fa(f'{metrics.p95:.0f}')} میلی‌ثانیه | ≤ {_fa(str(max_p95))} میلی‌ثانیه |",
        f"| p99 | {_fa(f'{metrics.p99:.0f}')} میلی‌ثانیه | — |",
        f"| میانگین | {_fa(f'{metrics.mean:.0f}')} میلی‌ثانیه | — |",
        "",
        "## تفکیک مسیرها",
        "",
        "| مسیر | تعداد | میانه | p95 | خطا |",
        "| --- | --- | --- | --- | --- |",
    ]
    by_label: dict[str, list[Sample]] = {}
    for sample in collector.samples:
        by_label.setdefault(sample.label, []).append(sample)
    for label, rows in sorted(by_label.items(), key=lambda item: -len(item[1])):
        values = sorted(row.millis for row in rows)
        errors = sum(1 for row in rows if row.error)
        lines.append(
            f"| {label} | {_fa(f'{len(rows):,}')} | {_fa(f'{_percentile(values, 0.5):.0f}')} "
            f"| {_fa(f'{_percentile(values, 0.95):.0f}')} | {_fa(str(errors))} |"
        )
    lines += ["", "## کدهای وضعیت", "", "| وضعیت | تعداد |", "| --- | --- |"]
    for status, count in Counter(sample.status for sample in collector.samples).most_common():
        lines.append(f"| {_fa(str(status))} | {_fa(f'{count:,}')} |")
    lines += [
        "",
        f"> تولید‌شده با `scripts/load_test.py` — {_fa(started.isoformat(timespec='seconds'))}",
    ]
    return lines


def _verdict(metrics: Metrics, args: argparse.Namespace) -> list[str]:
    """فهرست دلایل رد بارسنجی (خالی ⇒ پذیرفته)."""
    reasons: list[str] = []
    if metrics.server_errors:
        reasons.append(f"خطای ۵xx: {metrics.server_errors}")
    if metrics.error_rate > args.max_error_rate:
        reasons.append(f"نرخ خطا {metrics.error_rate * 100:.2f}٪ بیش از آستانه")
    if metrics.p95 > args.max_p95:
        reasons.append(f"p95 {metrics.p95:.0f}ms بیش از آستانه {args.max_p95}ms")
    return reasons


def run(args: argparse.Namespace) -> int:
    """اجرای بارسنجی و ساخت گزارش؛ کد خروج بر پایهٔ آستانه‌ها."""
    base_url = args.base_url.rstrip("/")
    headers = _parse_headers(args.header)

    # گرم‌کردن: نخستین درخواست باید کش/کارگر را آماده کند تا صدک‌ها واقعی باشند
    try:
        requests.get(f"{base_url}/health/ready/", headers=headers, timeout=10)
    except requests.RequestException as exc:
        print(f"❌ سامانه در دسترس نیست: {exc}", file=sys.stderr)
        return 2

    print(f"بارسنجی روی {base_url} — {args.users} کاربر، {args.seconds} ثانیه")
    started = _utc_now()
    elapsed = _drive_load(base_url, headers, args)
    collector: Collector = args.collector  # type: ignore[attr-defined]
    if not collector.samples:
        print("❌ هیچ نمونه‌ای ثبت نشد", file=sys.stderr)
        return 2

    metrics, _ = _summarise(collector, args.users, elapsed)
    report_text = "\n".join(_report_lines(metrics, collector, started, args.max_p95)) + "\n"
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
                    "users": metrics.users,
                    "seconds": metrics.elapsed,
                    "requests": metrics.total,
                    "rps": metrics.rps,
                    "error_rate": metrics.error_rate,
                    "server_errors": metrics.server_errors,
                    "p50_ms": metrics.p50,
                    "p90_ms": metrics.p90,
                    "p95_ms": metrics.p95,
                    "p99_ms": metrics.p99,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    reasons = _verdict(metrics, args)
    if reasons:
        print("❌ بارسنجی رد شد: " + " | ".join(reasons), file=sys.stderr)
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
