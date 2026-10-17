#!/usr/bin/env python3
"""بارسنجی سبک مهدیه — با کتابخانهٔ استاندارد، بدون وابستگی تازه.

چرا این اسکریپت هست؟
    «سایت سریع است» یک حس است، نه سند. این ابزار همان حس را به عدد تبدیل
    می‌کند: توان عبوری، صدک‌های تأخیر، توزیع کدهای وضعیت و — مهم‌تر — «حکم»
    با آستانهٔ ازپیش‌اعلام‌شده. اگر p95 از ۸۰۰ میلی‌ثانیه بگذرد یا یک ۵xx
    ببینیم، اسکریپت با کد خروج ۱ تمام می‌شود و اجازهٔ «سبز شدن» نمی‌دهد.

    دستهٔ درخواست‌ها همان چیزی است که یک روز زائرسرا می‌بیند: خانه، فهرست
    واحدها، صفحهٔ هر واحد، تقویم، تقویم API، ورود، نقطهٔ سلامت و «رزروهای من».
    مسیر نوشتن عمداً این‌جا نیست؛ ۲۰۰ درخواست موازیِ نوشتن در
    ``src/tests/test_concurrency.py`` آزموده می‌شود.

    نکتهٔ سنجش: ``urllib`` تغییرمسیرها را دنبال می‌کند (رفتار مرورگر واقعی)، پس
    «رزروهای من» برای کاربر ناشناس به‌صورت زنجیرهٔ تغییرمسیر → صفحهٔ ورود سنجیده
    می‌شود، نه به‌عنوان یک ۳۰۲ تنها.

دو قاعدهٔ خودِ این مخزن که در همین فایل رعایت شده است:
    • ``random`` ممنوع است ⇒ مولد شبه‌تصادفی قطعیِ کوچک (LCG) نوشته شده.
    • ``datetime.now`` ممنوع است ⇒ زمان از ``fromtimestamp(time.time(), tz=UTC)``.

نمونهٔ اجرا:
    python scripts/load_test.py --base-url http://127.0.0.1:8010 \\
        --users 32 --seconds 30 --secure --host-header mahdieh.ir \\
        --report docs/load/2026-10-16-load.md
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── دستهٔ درخواست‌ها: (نشانی، وزن) — وزن‌ها بازتاب ترافیک واقعی است ──────────
READ_SCENARIOS: Final[tuple[tuple[str, int], ...]] = (
    ("/", 30),
    ("/units/", 20),
    ("/units/A1/", 12),
    ("/units/B1/", 10),
    ("/calendar/", 8),
    ("/units/?days=30", 6),
    ("/api/v1/calendar?days=14", 6),
    ("/health/ready/", 4),
    ("/login/", 3),
    ("/my/", 3),
)
PERCENTILES: Final[tuple[int, ...]] = (50, 90, 95, 99)
DEFAULT_P95_BUDGET_MS: Final[float] = 800.0
DEFAULT_ERROR_BUDGET: Final[float] = 0.01
REQUEST_TIMEOUT_SECONDS: Final[float] = 20.0
WARMUP_SECONDS: Final[float] = 1.0
NETWORK_FAILURE: Final[int] = 0
SERVER_ERROR_FLOOR: Final[int] = 500
LCG_MODULUS: Final[int] = 2_147_483_647
LCG_MULTIPLIER: Final[int] = 48_271


class Lcg:
    """مولد شبه‌تصادفی قطعی و سبک — جایگزین ``random`` که در این مخزن ممنوع است.

    دو مزیت نسبت به ``random``: (۱) اجرای بارسنجی با همان دانه، همان دستهٔ
    درخواست‌ها را می‌سازد و گزارش‌ها قابل‌مقایسه می‌مانند؛ (۲) هر رشته نمونهٔ
    مستقل خودش را دارد، پس هیچ قفلی بین رشته‌ها لازم نیست.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = (abs(seed) % LCG_MODULUS) or 1

    def below(self, upper: int) -> int:
        """عدد صحیح در بازهٔ ``[0, upper)``."""
        self._state = (self._state * LCG_MULTIPLIER) % LCG_MODULUS
        return self._state % upper if upper > 0 else 0

    def pick(self, scenarios: tuple[tuple[str, int], ...]) -> str:
        """انتخاب وزنی یک نشانی از دسته."""
        total = sum(weight for _path, weight in scenarios)
        point = self.below(total)
        running = 0
        for path, weight in scenarios:
            running += weight
            if point < running:
                return path
        return scenarios[-1][0]


@dataclass(slots=True)
class Sample:
    """یک اندازه‌گیری: مسیر، کد وضعیت و تأخیر (میلی‌ثانیه).

    نگه‌داشتن **مسیر** قلبِ تشخیص است: بدون آن، گزارش می‌گوید «سایت کند است»
    و هیچ‌کس نمی‌داند کدام صفحه کند است. پوشش مسیر هزینه‌اش چند بایت حافظه است.
    """

    path: str
    status: int
    elapsed_ms: float


@dataclass(slots=True)
class Collector:
    """جمع‌آورندهٔ نمونه‌ها — هر رشته فقط لیست خودش را دست می‌زند (بی‌قفل)."""

    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        """افزودن یک نمونه."""
        self.samples.append(sample)


def _one_request(url: str, headers: dict[str, str], path: str = "") -> Sample:
    """یک درخواست GET با اندازه‌گیری تأخیر؛ خطای شبکه هم نمونه می‌سازد."""
    started = time.perf_counter()
    request = Request(url, headers=headers, method="GET")  # noqa: S310 - نشانی از آرگومان خط فرمان
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            response.read()
            status = int(response.status)
    except HTTPError as exc:
        status = int(exc.code)
    except (URLError, TimeoutError, OSError):
        status = NETWORK_FAILURE
    return Sample(
        path=path or url,
        status=status,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


def _worker(
    *,
    base_url: str,
    headers: dict[str, str],
    deadline: float,
    seed: int,
    box: Collector,
) -> None:
    """رشتهٔ بار: تا رسیدن مهلت، درخواست‌های وزنی می‌فرستد."""
    rng = Lcg(seed)
    while time.perf_counter() < deadline:
        path = rng.pick(READ_SCENARIOS)
        box.add(_one_request(f"{base_url}{path}", headers, path))


def _percentile(values: list[float], percentile: int) -> float:
    """صدک ساده (بدون وابستگی) — بارسنجی به دقت بیشتر از میلی‌ثانیه نیاز ندارد."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(percentile / 100 * len(ordered)) - 1))
    return ordered[rank]


def _fa(value: object) -> str:
    """ارقام فارسی برای گزارش فارسی — عدد لاتین در سند فارسی ناسازگار است."""
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _summarise(samples: list[Sample]) -> dict[str, object]:
    """خلاصهٔ آماری یک اجرا."""
    latencies = [sample.elapsed_ms for sample in samples if sample.status != NETWORK_FAILURE]
    statuses = Counter(sample.status for sample in samples)
    errors = sum(
        count
        for status, count in statuses.items()
        if status == NETWORK_FAILURE or status >= SERVER_ERROR_FLOOR
    )
    total = len(samples) or 1
    return {
        "total": len(samples),
        "statuses": {str(status): count for status, count in sorted(statuses.items())},
        "errors": errors,
        "error_rate": errors / total,
        "rps": 0.0,
        "latency": {
            f"p{percentile}": round(_percentile(latencies, percentile), 1)
            for percentile in PERCENTILES
        },
        "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0,
    }


def _per_path(samples: list[Sample]) -> list[dict[str, object]]:
    """خلاصهٔ هر مسیر، مرتب‌شده از گران‌ترین p95 — «کجا زمان می‌خورد؟»."""
    buckets: dict[str, list[Sample]] = {}
    for sample in samples:
        buckets.setdefault(sample.path, []).append(sample)
    rows: list[dict[str, object]] = []
    for path, items in buckets.items():
        latencies = [item.elapsed_ms for item in items if item.status != NETWORK_FAILURE]
        statuses = Counter(item.status for item in items)
        rows.append(
            {
                "path": path,
                "count": len(items),
                "p50": round(_percentile(latencies, 50), 1),
                "p95": round(_percentile(latencies, 95), 1),
                "max": round(max(latencies), 1) if latencies else 0.0,
                "statuses": {str(status): count for status, count in sorted(statuses.items())},
            }
        )
    rows.sort(key=lambda row: float(row["p95"]), reverse=True)
    return rows


def _capacity(
    summary: dict[str, object], *, users: int, cpus: int, p95_budget: float
) -> dict[str, object]:
    """مدل ظرفیت — «چند هسته لازم داریم؟»، بر پایهٔ قانون لیتل.

    سه عدد ساده اما تعیین‌کننده:

    ۱. **همزمانی مؤثر** = توان عبوری × تأخیر میانگین. اگر این عدد نزدیک تعداد
       کاربران باشد، یعنی همهٔ کاربران در صف هستند؛ پس سامانه در **سقف توان
       عبوری** است، نه گرفتار یک کوئری کند.
    ۲. **هزینهٔ CPU هر درخواست** = ظرفیت پردازنده ÷ توان عبوری. این عدد به
       دستگاه سنجش بسته است و در استقرار تولید با هسته‌های بیشتر بهتر می‌شود.
    ۳. **ظرفیت لازم** برای رعایت بودجه = (کاربران ÷ بودجه) × هزینهٔ CPU.
    """
    rps = float(summary["rps"])
    mean_ms = float(summary["mean"])
    cpu_ms_per_request = round(cpus * 1000 / rps, 1) if rps else 0.0
    required_rps = round(users / (p95_budget / 1000), 1) if p95_budget else 0.0
    required_cpus = round(required_rps * cpu_ms_per_request / 1000, 1)
    return {
        "effective_concurrency": round(rps * mean_ms / 1000, 1),
        "cpu_ms_per_request": cpu_ms_per_request,
        "required_rps_for_budget": required_rps,
        "required_cpus_for_budget": required_cpus,
        "measured_cpus": cpus,
    }


def _server_error_count(statuses: dict[str, int]) -> int:
    """شمار پاسخ‌های ۵xx — «خطای ما»، جدا از «خطای شبکهٔ سنجنده»."""
    return sum(count for status, count in statuses.items() if status.startswith("5"))


def _verdict(
    summary: dict[str, object], *, p95_budget: float, error_budget: float
) -> tuple[bool, list[str]]:
    """حکم نهایی با آستانه‌های اعلام‌شده — «سبز» فقط وقتی واقعاً سبز است."""
    reasons: list[str] = []
    latency = dict(summary["latency"])  # type: ignore[arg-type]
    statuses = dict(summary["statuses"])  # type: ignore[arg-type]
    p95 = float(latency["p95"])
    error_rate = float(summary["error_rate"])
    server_errors = _server_error_count(statuses)
    if p95 > p95_budget:
        reasons.append(f"p95 = {p95}ms از سقف {p95_budget}ms گذشت")
    if error_rate > error_budget:
        reasons.append(f"نرخ خطا = {error_rate * 100:.2f}٪ از سقف تجاوز کرد")
    if server_errors:
        reasons.append(f"{server_errors} پاسخ ۵xx دیده شد")
    return (not reasons), reasons


def _limit_lines(statuses: dict[str, int]) -> list[str]:
    """اگر محدودساز نرخ درگیر شده باشد، صریح بگوییم — نه اینکه زیر فرش بماند."""
    limited = sum(count for status, count in statuses.items() if status == "429")
    if not limited:
        return []
    return [
        "",
        "> **یادداشت ۴۲۹:** در این اجرا محدودساز نرخ درگیر شد. در بارسنجی همهٔ",
        "> درخواست‌ها از یک نشانی می‌آید، پس سهمِ پشت‌سرهمِ یک مبدأ پر می‌شود؛",
        "> در استقرار واقعی اگر چند زائر پشت یک شبکهٔ مشترک باشند همین می‌تواند",
        "> رخ دهد. سقف‌ها سیاست‌اند (`rate_limit.*`) و قابل تنظیم.",
    ]


def _per_path_rows(summary: dict[str, object]) -> list[dict[str, object]]:
    """ردیف‌های مسیر از خلاصه — با نوع‌های روشن برای گزارش‌ساز."""
    rows = summary.get("per_path")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _report_lines(
    *,
    summary: dict[str, object],
    users: int,
    seconds: float,
    base_url: str,
    base_url_extra: str,
    ok: bool,
    reasons: list[str],
    p95_budget: float,
    error_budget: float,
    capacity: dict[str, object] | None = None,
) -> list[str]:
    """ساخت گزارش مارک‌داون — همان سندی که در مخزن می‌ماند."""
    statuses = dict(summary["statuses"])  # type: ignore[arg-type]
    latency = dict(summary["latency"])  # type: ignore[arg-type]
    error_rate = float(summary["error_rate"]) * 100
    stamp = datetime.fromtimestamp(time.time(), tz=UTC).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# گزارش بارسنجی — {stamp} UTC",
        "",
        f"- نشانی هدف: `{base_url}`{base_url_extra}",
        f"- کاربران هم‌زمان: **{_fa(users)}**",
        f"- مدت بار: {_fa(seconds)} ثانیه",
        f"- کل درخواست‌ها: {_fa(summary['total'])}",
        f"- توان عبوری: **{_fa(round(float(summary['rps']), 1))} درخواست بر ثانیه**",
        f"- نرخ خطا (۵xx یا بی‌پاسخ): {_fa(f'{error_rate:.2f}')}٪",
        "",
        "## تأخیر",
        "",
        "| سنجه | مقدار | آستانه |",
        "|---|---|---|",
        f"| p50 | {_fa(latency['p50'])} میلی‌ثانیه | — |",
        f"| p90 | {_fa(latency['p90'])} میلی‌ثانیه | — |",
        f"| p95 | **{_fa(latency['p95'])} میلی‌ثانیه** | ≤ {_fa(p95_budget)} |",
        f"| p99 | {_fa(latency['p99'])} میلی‌ثانیه | — |",
        f"| میانگین | {_fa(summary['mean'])} میلی‌ثانیه | — |",
        "",
        "## کدهای وضعیت",
        "",
        "| کد | شمار |",
        "|---|---|",
    ]
    for status, count in sorted(statuses.items(), key=lambda item: item[0]):
        label = "بی‌پاسخ (شبکه)" if status == str(NETWORK_FAILURE) else status
        lines.append(f"| {_fa(label)} | {_fa(count)} |")

    # تفکیک بر پایهٔ مسیر — بخش تشخیصی گزارش.
    rows = _per_path_rows(summary)
    if rows:
        lines += [
            "",
            "## تفکیک بر پایهٔ مسیر (کجا زمان می‌خورد؟)",
            "",
            "| مسیر | شمار | p50 | p95 | بیشینه | کدها |",
            "|---|---|---|---|---|---|",
        ]
        for row in rows:
            codes = " · ".join(f"{key}×{_fa(value)}" for key, value in row["statuses"].items())  # type: ignore[union-attr]
            lines.append(
                f"| `{row['path']}` | {_fa(row['count'])} | {_fa(row['p50'])} | "
                f"{_fa(row['p95'])} | {_fa(row['max'])} | {_fa(codes)} |"
            )

    if capacity:
        lines += [
            "",
            "## ظرفیت و گلوگاه (قانون لیتل)",
            "",
            f"- همزمانی مؤثر: **{_fa(capacity['effective_concurrency'])}** از {_fa(users)} کاربر",
            f"- هزینهٔ پردازشی هر درخواست: {_fa(capacity['cpu_ms_per_request'])} میلی‌ثانیه‌هسته",
            f"- ظرفیت لازم برای رعایت بودجه: **{_fa(capacity['required_rps_for_budget'])} "
            f"درخواست بر ثانیه** ≈ {_fa(capacity['required_cpus_for_budget'])} هسته "
            f"(سنجش روی {_fa(capacity['measured_cpus'])} هسته)",
        ]
        # «در صف کامل» یعنی همزمانی مؤثر نزدیک به شمار کاربران است. ۱۵٪ رواداری
        # لازم است: هر درخواست چند میلی‌ثانیه صرف سرصفحه و شبکه می‌کند، پس
        # همزمانی سنجیده‌شده همیشه کمی زیر شمار کاربران می‌ماند.
        close = abs(float(capacity["effective_concurrency"]) - users) <= max(3.0, users * 0.15)
        lines.append(
            "- تفسیر: "
            + (
                "همهٔ کاربران در صف‌اند ⇒ سامانه در **سقف توان عبوری** است؛ "
                "راه‌حل، هستهٔ بیشتر یا کارِ سبک‌تر است، نه شکارِ یک کوئری کند."
                if close
                else "کاربران در صف کامل نیستند ⇒ گلوگاه نقطه‌ای است؛ به جدول مسیرها نگاه کنید."
            )
        )

    spread = summary.get("p95_spread")
    if isinstance(spread, dict):
        lines += [
            "",
            "## پراکندگی بین تکرارها",
            "",
            f"- p95 در {_fa(spread['passes'])} اجرا: کمینه {_fa(spread['min'])} · "
            f"بیشینه {_fa(spread['max'])} میلی‌ثانیه",
            "- عدد گزارش بالا **میانه** است. پراکندگی زیاد یعنی سرور سنجش با بار",
            "  دیگری شریک است؛ در آن حالت عدد را قطعی نپندارید.",
        ]

    lines += _limit_lines(statuses)
    lines += ["", f"## حکم: {'پذیرفته ✅' if ok else 'رد ❌'}", ""]
    lines += [f"- {reason}" for reason in reasons]
    if ok:
        lines.append(
            f"- هیچ ۵xx نبود، نرخ خطا زیر {_fa(f'{error_budget * 100:.0f}')}٪ ماند و p95 "
            f"زیر {_fa(p95_budget)} میلی‌ثانیه بود."
        )
    return lines


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """تفسیر گزینه‌های خط فرمان."""
    parser = argparse.ArgumentParser(description="بارسنجی سبک سامانهٔ مهدیه")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010", help="نشانی پایهٔ سرور")
    parser.add_argument("--users", type=int, default=16, help="تعداد کاربران هم‌زمان")
    parser.add_argument("--seconds", type=float, default=20.0, help="مدت بار (ثانیه)")
    parser.add_argument("--ramp", type=float, default=2.0, help="مدت افزایش پله‌ای کاربران (ثانیه)")
    parser.add_argument("--seed", type=int, default=20261016, help="دانهٔ مولد قطعی")
    parser.add_argument("--secure", action="store_true", help="افزودن X-Forwarded-Proto: https")
    parser.add_argument("--host-header", default="", help="سرصفحهٔ Host (در استقرار تولید لازم است)")
    parser.add_argument("--max-p95", type=float, default=DEFAULT_P95_BUDGET_MS, help="سقف p95 (ms)")
    parser.add_argument(
        "--max-error-rate", type=float, default=DEFAULT_ERROR_BUDGET, help="سقف نرخ خطا"
    )
    parser.add_argument(
        "--server-cpus",
        type=int,
        default=os.cpu_count() or 1,
        help="هستهٔ پردازندهٔ سرور هدف (برای مدل ظرفیت)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="تعداد تکرار اجرا؛ گزارش روی میانهٔ تکرارها نوشته می‌شود (سرور مشترک)",
    )
    parser.add_argument("--report", default="", help="مسیر گزارش مارک‌داون")
    parser.add_argument("--json", action="store_true", help="چاپ خروجی JSON")
    return parser.parse_args(argv)


def _run_once(
    options: argparse.Namespace, headers: dict[str, str]
) -> tuple[dict[str, object], float, dict[str, object]]:
    """یک پاسِ بار: نمونه‌ها، خلاصه، مدل ظرفیت.

    چرا جدا شده؟ چون در سرور اشتراکی، یک پاسِ تنها می‌تواند خوش‌شانس یا
    بدشانس باشد؛ جداکردن پاس، «میانهٔ چند اجرا» را ممکن می‌کند.
    """
    collectors = [Collector() for _ in range(options.users)]
    started = time.perf_counter()
    deadline = started + options.seconds
    threads: list[threading.Thread] = []
    for index, collector in enumerate(collectors):
        thread = threading.Thread(
            target=_worker,
            kwargs={
                "base_url": options.base_url,
                "headers": headers,
                "deadline": deadline,
                "seed": options.seed + index * 7919,
                "box": collector,
            },
            daemon=True,
        )
        threads.append(thread)
        thread.start()
        if options.ramp > 0:
            time.sleep(options.ramp / max(1, options.users))
    for thread in threads:
        thread.join(timeout=options.seconds + REQUEST_TIMEOUT_SECONDS)
    elapsed = max(0.001, time.perf_counter() - started)

    samples = [sample for collector in collectors for sample in collector.samples]
    summary = _summarise(samples)
    summary["rps"] = round(len(samples) / elapsed, 1)
    summary["per_path"] = _per_path(samples)
    capacity = _capacity(
        summary,
        users=options.users,
        cpus=options.server_cpus,
        p95_budget=options.max_p95,
    )
    summary["capacity"] = capacity
    return summary, elapsed, capacity


def main(argv: list[str] | None = None) -> int:
    """اجرای بارسنجی و بازگرداندن کد خروج."""
    options = _parse_args(argv)
    headers = {"User-Agent": "mahdieh-load/1.0", "Accept": "text/html,application/json"}
    if options.secure:
        headers["X-Forwarded-Proto"] = "https"
    if options.host_header:
        headers["Host"] = options.host_header

    # گرم‌کردن: اولین درخواست هر فرآیند، هزینهٔ راه‌اندازی و کش سرد دارد و نباید
    # در آمار بار بیاید — وگرنه گزارش، «سردی کش» را «کندی سامانه» می‌نامد.
    warmup = _one_request(f"{options.base_url}/health/ready/", headers)
    if warmup.status == NETWORK_FAILURE:
        print(f"سامانه در دسترس نیست: {options.base_url}")
        return 2
    time.sleep(WARMUP_SECONDS)

    passes: list[tuple[dict[str, object], dict[str, object]]] = []
    for index in range(max(1, options.repeats)):
        if index:
            time.sleep(WARMUP_SECONDS)
        summary, _elapsed, capacity = _run_once(options, headers)
        passes.append((summary, capacity))
        if options.repeats > 1 and not options.json:
            print(f"پاس {index + 1}/{options.repeats}: p95={dict(summary['latency'])['p95']}ms")  # type: ignore[arg-type]

    # میانهٔ پاس‌ها: در سرور مشترک، میانه از «بهترین پاس» صادقانه‌تر است.
    ordered = sorted(passes, key=lambda item: float(dict(item[0]["latency"])["p95"]))  # type: ignore[arg-type]
    summary, capacity = ordered[len(ordered) // 2]
    spread = [float(dict(item[0]["latency"])["p95"]) for item in ordered]  # type: ignore[arg-type]
    if len(spread) > 1:
        summary["p95_spread"] = {"min": min(spread), "max": max(spread), "passes": len(spread)}
    ok, reasons = _verdict(summary, p95_budget=options.max_p95, error_budget=options.max_error_rate)

    if options.json:
        print(json.dumps({"summary": summary, "ok": ok, "reasons": reasons}, ensure_ascii=False))
    else:
        latency = dict(summary["latency"])  # type: ignore[arg-type]
        print(f"کل درخواست‌ها: {summary['total']}  ·  RPS: {summary['rps']}")
        print(f"p50/p95/p99: {latency['p50']}/{latency['p95']}/{latency['p99']} ms")
        print(f"نرخ خطا: {float(summary['error_rate']) * 100:.2f}%")
        print(
            f"ظرفیت: همزمانی مؤثر {capacity['effective_concurrency']}  ·  "
            f"CPU هر درخواست {capacity['cpu_ms_per_request']}ms  ·  "
            f"لازم برای بودجه ≈ {capacity['required_cpus_for_budget']} هسته"
        )
        print("حکم: " + ("پذیرفته ✅" if ok else "رد ❌ — " + "؛ ".join(reasons)))

    if options.report:
        extra = f" (سرصفحهٔ Host: `{options.host_header}`)" if options.host_header else ""
        lines = _report_lines(
            summary=summary,
            users=options.users,
            seconds=options.seconds,
            base_url=options.base_url,
            base_url_extra=extra,
            ok=ok,
            reasons=reasons,
            p95_budget=options.max_p95,
            error_budget=options.max_error_rate,
            capacity=capacity,
        )
        Path(options.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"گزارش نوشته شد: {options.report}")

    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - ورودی خط فرمان
    raise SystemExit(main())
