"""پایشگر سلامت — «سایت زنده است؟» با کد خروجی که هشدار واقعی می‌سازد.

چرا فرمان جدا، وقتی ``/health/ready/`` هست؟ چون **دیدن** با **هشدار گرفتن** یکی
نیست: نمای آمادگی فقط وقتی پاسخ می‌دهد که کسی بپرسد. این فرمان همان داوری را
می‌گیرد، آن را با کد خروجی به systemd می‌رساند (تا ``systemctl --failed`` آن را
نشان دهد) و — مهم‌تر — **سه بار در دقیقه برای یک خرابی پیام نمی‌دهد**: هشدار
فقط هنگام *تغییر وضعیت* می‌رود، پس کسی صدایش را خفه نمی‌کند.

کدهای خروجی (قرارداد، در سند پایش هم آمده است):

| کد | معنا | کار درست |
| --- | --- | --- |
| ۰ | سالم | هیچ |
| ۱ | افت‌کرده (کش/ذخیره‌سازی) | سر فرصت نگاه کنید؛ ترافیک بماند |
| ۲ | ناسالم (پایگاه‌داده/مهاجرت) | همان لحظه: ``incident-response.md`` بخش ۳ |

``--json`` خروجی ماشین‌خوان می‌دهد (برای Zabbix/Prometheus textfile/Uptime-Kuma)؛
``--quiet`` در حالت سالم هیچ چاپ نمی‌کند تا لاگ systemd تمیز بماند.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import mail_admins
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

#: کدهای خروجی — بخشی از قرارداد عمومی این فرمان (systemd/پایش روی آن حساب می‌کند).
EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_UNHEALTHY = 2

#: جای پیش‌فرض پروندهٔ وضعیت: کنار مخزن، در پوشهٔ ``var`` (همان‌جا که systemd اجازهٔ
#: نوشتن دارد). محتوایش وضعیت آخرین اجرا است و برای «تغییر وضعیت» لازم است.
DEFAULT_STATE_FILE = Path(settings.BASE_DIR).parent / "var" / "healthcheck.state"

_HTTP_TIMEOUT_SECONDS = 10
#: در خرابیِ پایدار، هر چند ساعت یک بار یادآوری شود (۰ = هرگز). هشدارِ هر ۵ دقیقه
#: یعنی تیمی که هشدارها را می‌بندد؛ یادآوری هر ۱۲ ساعت یعنی حادثهٔ طولانی فراموش
#: نمی‌شود. این «درستیِ سکوت» است.
DEFAULT_RENOTIFY_HOURS = 12.0
#: کدهای وضعیت «ناسالم» HTTP — هر چیز دیگری (۲۰۰ و ۳۰۳) «افت‌کرده» شمرده می‌شود،
#: چون یعنی نقطهٔ پایانی پاسخ داد ولی آنچه انتظار داشتیم نبود.
_UNHEALTHY_HTTP = 503


class _ProbeError(Exception):
    """نرسیدن به نقطهٔ پایانی سلامت — خودش یک خرابی است، نه «سالم»."""


class Command(BaseCommand):
    """اجرای یک پایش سلامت و هشداردهی فقط هنگام تغییر وضعیت."""

    help = "پایش سلامت سامانه با کد خروجی ماشین‌خوان و هشدار ضد-اسپم"

    def add_arguments(self, parser: CommandParser) -> None:
        """پرچم‌های فرمان — هر پرچم یک تصمیم عملیاتی است، نه یک گزینهٔ تزئینی."""
        parser.add_argument(
            "--base-url",
            default="",
            help=(
                "اگر بدهید، از راه HTTP پایش می‌شود (مسیر واقعی nginx→gunicorn). "
                "اگر ندهید، همان داوری درون‌فرآیندی اجرا می‌شود."
            ),
        )
        parser.add_argument(
            "--behind-proxy",
            action="store_true",
            help="افزودن سرآیند X-Forwarded-Proto: https (وقتی پشت nginx/TLS هستیم).",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="فرستادن ایمیل به ADMINS هنگام تغییر وضعیت (نه هر بار).",
        )
        parser.add_argument(
            "--state-file",
            default=str(DEFAULT_STATE_FILE),
            help="پروندهٔ وضعیت برای تشخیص «تغییر» (پیش‌فرض: var/healthcheck.state).",
        )
        parser.add_argument(
            "--renotify-after",
            type=float,
            default=DEFAULT_RENOTIFY_HOURS,
            help=(
                "در خرابیِ پایدار، هر چند ساعت یک بار یادآوری شود (۰ = هرگز). "
                "پیش‌فرض ۱۲ ساعت: نه اسپم می‌سازد، نه حادثهٔ طولانی فراموش می‌شود."
            ),
        )
        parser.add_argument("--json", action="store_true", dest="as_json", help="خروجی ماشین‌خوان.")
        parser.add_argument(
            "--quiet", action="store_true", help="در حالت سالم هیچ چاپ نکن (مناسب systemd)."
        )

    # ── سنجش ────────────────────────────────────────────────────────────────
    def _probe_http(self, base_url: str, *, behind_proxy: bool) -> tuple[dict[str, Any], int]:
        """پرسش از نقطهٔ پایانی آمادگی روی سرویس در حال اجرا."""
        url = base_url.rstrip("/") + "/health/ready/"
        scheme = urlparse(url).scheme
        if scheme not in {"http", "https"}:
            # طرح‌های دیگری مثل ``file://`` می‌توانند پروندهٔ سرور را بخوانند؛ نشانی
            # پایش باید همیشه وب باشد. (B310 با همین محدودسازی خنثی می‌شود.)
            msg = f"نشانی پایش باید http یا https باشد، نه «{scheme}»"
            raise _ProbeError(msg)
        headers = {"User-Agent": "mahdieh-healthcheck/1", "Accept": "application/json"}
        if behind_proxy:
            headers["X-Forwarded-Proto"] = "https"
        request = Request(url, headers=headers)  # noqa: S310 - نشانی از پیکربندی خودمان
        try:
            with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
                body = response.read().decode("utf-8")
                payload = json.loads(body)
                return payload, response.status
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {"status": "unhealthy", "checks": {}, "raw": body[:200]}
            return payload, exc.code
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            msg = f"نرسیدن به {url}: {type(exc).__name__}"
            raise _ProbeError(msg) from exc

    def _probe_in_process(self) -> tuple[dict[str, Any], int]:
        """همان داوری نمای آمادگی، بدون HTTP (یک منبع حقیقت)."""
        from apps.core.health import readiness_payload

        return readiness_payload()

    # ── وضعیت پیشین (برای هشدار ضد-اسپم) ────────────────────────────────────
    @staticmethod
    def _read_state(state_file: Path) -> dict[str, Any]:
        """وضعیت آخرین اجرا؛ نبود/خرابی پرونده یعنی «نمی‌دانم» (دیکشنری خالی)."""
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_state(state_file: Path, *, status: str, detail: str, notified_at: str) -> None:
        """ثبت وضعیت این اجرا (بی‌صدا در ناکامی: پایش نباید خودش خرابی بسازد)."""
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps(
                    {
                        "status": status,
                        "detail": detail,
                        "checked_at": timezone.now().isoformat(),
                        "notified_at": notified_at,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def _should_notify(
        *, status: str, previous: str | None, notified_at: str, renotify_after: float
    ) -> bool:
        """آیا این اجرا باید ایمیل بفرستد؟ (قرارداد ضد-اسپم).

        سه حالت هشدار: تغییر وضعیت، بهبودی، و یادآوریِ دیرهنگام در خرابیِ پایدار.
        """
        if status != previous:
            return True
        if status == "healthy":
            return False
        if renotify_after <= 0:
            return False
        try:
            last = datetime.fromisoformat(notified_at)
        except (TypeError, ValueError):
            return True  # زمان نامعلوم ⇒ محافظه‌کارانه هشدار بده
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        elapsed_hours = (timezone.now() - last).total_seconds() / 3600
        return elapsed_hours >= renotify_after

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - امضای جنگو
        """اجرای پایش؛ کد خروج از ``sys.exit`` با کد قراردادی می‌آید."""
        base_url: str = options["base_url"]
        behind_proxy: bool = options["behind_proxy"]
        notify: bool = options["notify"]
        as_json: bool = options["as_json"]
        quiet: bool = options["quiet"]
        state_file = Path(options["state_file"])

        try:
            payload, http_status = (
                self._probe_http(base_url, behind_proxy=behind_proxy)
                if base_url
                else self._probe_in_process()
            )
        except _ProbeError as exc:
            payload, http_status = {"status": "unhealthy", "checks": {}, "degraded": []}, 0
            payload["error"] = str(exc)

        status = str(payload.get("status", "unhealthy"))
        if http_status == _UNHEALTHY_HTTP:
            status = "unhealthy"
        exit_code = {"healthy": EXIT_HEALTHY, "degraded": EXIT_DEGRADED}.get(status, EXIT_UNHEALTHY)

        detail = self._detail(payload)
        state = self._read_state(state_file)
        previous = state.get("status") if isinstance(state.get("status"), str) else None
        changed = previous != status
        should_notify = notify and self._should_notify(
            status=status,
            previous=previous,
            notified_at=str(state.get("notified_at") or ""),
            renotify_after=float(options["renotify_after"]),
        )
        if should_notify:
            self._notify(status=status, detail=detail, previous=previous)
        notified_at = (
            timezone.now().isoformat() if should_notify else str(state.get("notified_at") or "")
        )
        self._write_state(state_file, status=status, detail=detail, notified_at=notified_at)

        if as_json:
            self.stdout.write(
                json.dumps(
                    {
                        "status": status,
                        "http_status": http_status,
                        "detail": detail,
                        "changed": changed,
                        "previous": previous,
                        "exit_code": exit_code,
                        "checked_at": timezone.now().isoformat(),
                    },
                    ensure_ascii=False,
                )
            )
        elif not quiet or status != "healthy":
            marker = {"healthy": "✅", "degraded": "🟠"}.get(status, "🔴")
            self.stdout.write(f"{marker} سلامت: {status} — {detail} (کد خروج {exit_code})")
            if changed and previous is not None:
                self.stdout.write(f"   تغییر وضعیت: {previous} → {status}")

        raise SystemExit(exit_code)

    @staticmethod
    def _detail(payload: dict[str, Any]) -> str:
        """جملهٔ فارسی خوانا از گزارش (برای ایمیل و لاگ)."""
        if error := payload.get("error"):
            return str(error)
        failed = payload.get("failed") or []
        degraded = payload.get("degraded") or []
        if failed:
            return "ناسالم: " + "، ".join(str(name) for name in failed)
        if degraded:
            return "افت‌کرده: " + "، ".join(str(name) for name in degraded)
        return "همهٔ سنجه‌ها سالم"

    @staticmethod
    def _notify(*, status: str, detail: str, previous: str | None) -> None:
        """ایمیل هشدار به ADMINS؛ خرابی خودِ ایمیل، هشدار را پنهان نمی‌کند."""
        subject = f"[مهدیه] وضعیت سلامت سامانه: {status}"
        if status == "healthy":
            subject = "[مهدیه] سامانه به وضعیت سالم بازگشت"
        body = (
            f"وضعیت پیشین: {previous or 'نامعلوم'}\n"
            f"وضعیت کنونی: {status}\n"
            f"جزئیات: {detail}\n\n"
            "رانبوک: docs/runbooks/monitoring.md و بخش ۳ از incident-response.md\n"
        )
        try:
            # بدون ``fail_silently``: در Django 6.1 همین آرگومان منسوخ شده است و
            # خرابی ایمیل هم باید در لاگ دیده شود، نه بی‌صدا بلعیده شود.
            mail_admins(subject, body)
        except Exception as exc:  # pragma: no cover - فقط در خرابی زیرساخت ایمیل
            import structlog

            structlog.get_logger(__name__).warning(
                "healthcheck_notify_failed", error=type(exc).__name__
            )
