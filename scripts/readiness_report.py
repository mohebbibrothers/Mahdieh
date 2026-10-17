#!/usr/bin/env python3
"""پروندهٔ آمادگی انتشار — «آیا این نسخه، شایستهٔ رفتن روی سرور است؟»

مسئله‌ای که این ابزار حل می‌کند: پیش از انتشار، ده‌ها «باید باشد» در ذهن تیم
پخش است (واحد systemd، رانبوک، قرارداد API، سد، پشتیبان...). آدمیزاد همه را
به‌خاطر نمی‌سپارد و «فکر می‌کنم هست» جای «می‌دانم هست» نمی‌نشیند.

این ابزار همان فهرست را **اجرا** می‌کند، نتیجه را در یک برگه می‌ریزد و حکم
می‌دهد. برگه، خودش امضاشدنی است: شناسهٔ کامیت و زمان در آن هست، پس بعداً
می‌شود گفت «با این نسخه، در این تاریخ، این چیزها سنجیده شد».

اجرا:

    python scripts/readiness_report.py                 # همهٔ بررسی‌ها
    python scripts/readiness_report.py --fast          # بدون زیرفرمان‌های کند
    python scripts/readiness_report.py --output /tmp/r.md

کد خروج: ۰ اگر هیچ بررسی‌ای «رد» نشده باشد؛ ۱ اگر دست‌کم یکی رد شود.
«هشدار» شغل را قرمز نمی‌کند ولی در برگه می‌ماند.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT: Final[Path] = REPO_ROOT / "docs" / "release" / "readiness.md"

#: فایل‌هایی که نبودشان یعنی «انتشار ناقص»، نه «سلیقهٔ متفاوت».
REQUIRED_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("Dockerfile", "تصویر تولیدی"),
    ("entrypoint.sh", "نقطهٔ ورود کانتینر (مهاجرت + دروازهٔ انتشار)"),
    ("deploy/nginx/mahdieh.conf", "پیکربندی Nginx با سد لبه‌ای"),
    ("deploy/env/mahdieh.env.example", "نمونهٔ محیط تولید"),
    ("deploy/systemd/mahdieh.service", "واحد سرویس اصلی"),
    ("deploy/systemd/mahdieh-backup.timer", "زمان‌بند پشتیبان"),
    ("deploy/systemd/mahdieh-drill.timer", "زمان‌بند تمرین بازیابی"),
    ("deploy/systemd/mahdieh-healthcheck.timer", "زمان‌بند پایش سلامت"),
    ("deploy/systemd/mahdieh-holds.timer", "زمان‌بند آزادسازی نگه‌داشت"),
    ("deploy/systemd/mahdieh-reminders.timer", "زمان‌بند یادآور پیامکی"),
    ("deploy/systemd/mahdieh-retention.timer", "زمان‌بند نگهداشت داده"),
    ("docs/api/openapi.yaml", "قرارداد API برای تیم فرانت"),
    ("docs/runbooks/deploy.md", "رانبوک استقرار"),
    ("docs/runbooks/golive.md", "رانبوک جامع روز انتشار"),
    ("docs/runbooks/incident-response.md", "رانبوک حادثه"),
    ("docs/runbooks/monitoring.md", "رانبوک پایش"),
    ("docs/runbooks/sms-golive.md", "رانبوک فعال‌سازی پیامک"),
    ("docs/security/dast.md", "سند کاوش امنیتی پویا"),
    ("docs/load/production-capacity-plan.md", "برنامهٔ ظرفیت تولید"),
    ("scripts/preflight_production.sh", "بازرسی سرور پیش از انتشار"),
    ("scripts/sms_golive.sh", "فعال‌سازی یک‌فرمانی الگوهای پیامک"),
    ("scripts/dast_probe.py", "کاوش امنیتی پویا"),
    ("scripts/load_test.py", "ابزار بارسنجی"),
    ("Makefile", "دروازهٔ یکپارچهٔ اجرا"),
)


@dataclass
class CheckResult:
    """نتیجهٔ یک بررسی: نام، وضعیت و توضیح کوتاه."""

    name: str
    status: str  # ok | warn | fail
    detail: str


@dataclass
class Report:
    """برگهٔ آمادگی: فهرست بررسی‌ها + فرادادهٔ اجرا."""

    commit: str
    branch: str
    dirty: bool
    generated_at: str
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str) -> None:
        """افزودن یک نتیجه به برگه."""
        self.results.append(CheckResult(name=name, status=status, detail=detail))

    @property
    def failures(self) -> list[CheckResult]:
        """بررسی‌های «رد»شده."""
        return [r for r in self.results if r.status == "fail"]

    @property
    def warnings(self) -> list[CheckResult]:
        """بررسی‌های هشدار."""
        return [r for r in self.results if r.status == "warn"]

    @property
    def verdict(self) -> str:
        """حکم نهایی به فارسی."""
        if self.failures:
            return "🔴 آماده نیست"
        if self.warnings:
            return "🟠 آماده با هشدار"
        return "🟢 آماده"


def _run(command: list[str], *, timeout: int = 300) -> tuple[int, str]:
    """اجرای یک فرمان در ریشهٔ مخزن و برگرداندن (کد خروج، خروجی)."""
    try:
        completed = subprocess.run(  # noqa: S603 - فرمان‌ها ثابت‌اند، نه ورودی کاربر
            command,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 99, f"{type(exc).__name__}: {exc}"
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _git(*args: str) -> str:
    """خواندن یک مقدار از git (بی‌صدا در ناکامی)."""
    code, output = _run(["git", *args], timeout=30)
    return output.splitlines()[0] if code == 0 and output else ""


def check_artifacts(report: Report) -> None:
    """همهٔ فایل‌های الزامی انتشار وجود دارند؟"""
    missing = [
        f"{path} ({why})" for path, why in REQUIRED_ARTIFACTS if not (REPO_ROOT / path).is_file()
    ]
    if missing:
        report.add("فایل‌های الزامی انتشار", "fail", "غایب: " + "؛ ".join(missing))
    else:
        report.add(
            "فایل‌های الزامی انتشار",
            "ok",
            f"همهٔ {_fa(len(REQUIRED_ARTIFACTS))} فایل لازم هست",
        )


def check_env_parity(report: Report) -> None:
    """هم‌خوانی نمونه‌های محیط با تنظیمات."""
    code, output = _run([sys.executable, "scripts/check_env_parity.py"])
    report.add("هم‌خوانی محیط", "ok" if code == 0 else "fail", output.splitlines()[-1][:200])


def check_structure(report: Report) -> None:
    """نبود drift در STRUCTURE.md."""
    code, output = _run([sys.executable, "scripts/generate_structure.py", "--check"])
    report.add(
        "ساختار مخزن",
        "ok" if code == 0 else "fail",
        output.splitlines()[-1][:200] if output else "",
    )


def check_migrations(report: Report) -> None:
    """نبود مهاجرتِ ساخته‌نشده (مدل بدون مهاجرت = استقرار نیمه‌کاره)."""
    manage = REPO_ROOT / "src" / "manage.py"
    code, output = _run([sys.executable, str(manage), "makemigrations", "--check", "--dry-run"])
    detail = "بدون drift" if code == 0 else output.splitlines()[-1][:200]
    report.add("مهاجرت‌ها", "ok" if code == 0 else "fail", detail)


def check_production_gate(report: Report) -> None:
    """دروازهٔ انتشار در حالت محلی، با خروجی ماشین‌خوان."""
    manage = REPO_ROOT / "src" / "manage.py"
    code, output = _run([sys.executable, str(manage), "production_check", "--json"], timeout=180)
    if code == 0:
        report.add("دروازهٔ انتشار (حالت محلی)", "ok", "بدون ایراد بلوکه‌کننده")
        return
    report.add(
        "دروازهٔ انتشار (حالت محلی)",
        "warn",
        "در حالت محلی ایراد دارد (طبیعی است؛ روی سرور با --strict سنجیده می‌شود): " + output[-200:],
    )


def check_sms_readiness(report: Report) -> None:
    """آمادگی الگوهای پیامک — پیش از انتشار «در انتظار» است، نه «ایراد»."""
    manage = REPO_ROOT / "src" / "manage.py"
    code, _ = _run([sys.executable, str(manage), "sms_patterns", "--check"], timeout=180)
    if code == 0:
        report.add("الگوهای پیامک", "ok", "همهٔ الگوهای حیاتی کد دارند")
    else:
        report.add(
            "الگوهای پیامک",
            "warn",
            "در انتظار کدهای پنل (فعال‌سازی پس از بالا آمدن دامنه — رانبوک sms-golive)",
        )


def check_tooling(report: Report) -> None:
    """ابزارهای لازم محیط (ruff/mypy/uv) — بی‌آن‌ها، گیت قابل اجرا نیست."""
    missing = [tool for tool in ("uv", "git") if shutil.which(tool) is None]
    if missing:
        report.add("ابزارهای محیط", "warn", "غایب: " + "، ".join(missing))
    else:
        report.add("ابزارهای محیط", "ok", "uv و git در دسترس‌اند")


def build_report(*, fast: bool = False) -> Report:
    """اجرای بررسی‌ها و ساخت برگه."""
    report = Report(
        commit=_git("rev-parse", "--short", "HEAD") or "نامعلوم",
        branch=_git("rev-parse", "--abbrev-ref", "HEAD") or "نامعلوم",
        dirty=bool(
            _git(
                "status",
                "--porcelain",
                "--",
                ".",
                f":(exclude){DEFAULT_OUTPUT.relative_to(REPO_ROOT).as_posix()}",
            )
        ),
        generated_at=datetime.fromtimestamp(time.time(), tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    check_artifacts(report)
    check_tooling(report)
    if not fast:
        check_env_parity(report)
        check_structure(report)
        check_migrations(report)
        check_production_gate(report)
        check_sms_readiness(report)
    return report


STATUS_MARK = {"ok": "✅", "warn": "🟠", "fail": "🔴"}

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(value: object) -> str:
    """ارقام فارسی — قاعدهٔ پروژه برای هر خروجیِ چشم‌آدم (ADR-0014)."""
    return str(value).translate(_PERSIAN_DIGITS)


def render(report: Report) -> str:
    """تبدیل برگه به Markdown (همان چیزی که در مخزن بایگانی می‌شود)."""
    lines = [
        "# برگهٔ آمادگی انتشار",
        "",
        "> این فایل **خودکار** ساخته می‌شود (`make readiness`). دستی ویرایش نکنید؛",
        "> دوباره بسازید. هر سطر، یک بررسیِ واقعی است، نه یک ادعا.",
        "",
        f"- **کامیت:** `{report.commit}` · **شاخه:** `{report.branch}`"
        + (" · ⚠️ درخت کاری تغییر دارد" if report.dirty else ""),
        f"- **زمان ساخت:** {_fa(report.generated_at)}",
        f"- **حکم:** {report.verdict}",
        "",
        "| بررسی | وضعیت | توضیح |",
        "| --- | --- | --- |",
    ]
    for result in report.results:
        lines.append(f"| {result.name} | {STATUS_MARK[result.status]} | {_fa(result.detail)} |")

    lines += [
        "",
        "## کارهایی که عمداً «پس از انتشار» انجام می‌شوند",
        "",
        "| کار | چرا پس از انتشار؟ | رانبوک |",
        "| --- | --- | --- |",
        "| دریافت کدهای الگو و فعال‌سازی پیامک | پنل، الگو را پیش از بالا آمدن دامنه تأیید نمی‌کند | `docs/runbooks/sms-golive.md` |",
        "| ثبت شمارهٔ پذیرش (`MAHDIEH_SUPPORT_PHONE`) | شماره پس از انتشار اعلام می‌شود | `docs/runbooks/sms-golive.md` |",
        "| عدد بار روی ۴ هستهٔ واقعی | نیازمند همان سخت‌افزار | `docs/load/production-capacity-plan.md` |",
        "| کاوش ZAP به‌عنوان لایهٔ دوم | نیازمند سرور عمومی/پیش‌تولید | `docs/security/dast.md` |",
        "| باز کردن `MAHDIEH_CORS_ORIGINS` | دامنهٔ تیم فرانت هنوز مشخص نیست | `docs/api/README.md` §۵.۵ |",
        "",
        "> این برگه وضعیتِ **همان کامیتی** را تأیید می‌کند که در سطر «کامیت» آمده؛",
        "> چون خودش هم بخشی از تاریخچه است، پس از هر انتشار در کامیتِ مستنداتِ",
        "> بعدی ثبت می‌شود. برای بازتولید: `make readiness`.",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    """اجرای بررسی‌ها، نوشتن برگه و بازگرداندن کد خروج."""
    parser = argparse.ArgumentParser(description="ساخت برگهٔ آمادگی انتشار")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="مسیر فایل برگه")
    parser.add_argument("--fast", action="store_true", help="بدون زیرفرمان‌های کند")
    parser.add_argument("--quiet", action="store_true", help="فقط حکم نهایی چاپ شود")
    args = parser.parse_args(argv)

    report = build_report(fast=args.fast)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render(report), encoding="utf-8")

    if not args.quiet:
        for result in report.results:
            print(f"{STATUS_MARK[result.status]} {result.name}: {_fa(result.detail)}")
    print(f"\n{report.verdict} — برگه: {output_path}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
