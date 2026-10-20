"""mahdieh — فرمان یکپارچهٔ مدیریت مهدیه روی میزبان cPanel.

    mahdieh                        داشبورد: نسخه‌ها، سلامت، نسخهٔ تازه، پشتیبان، دیسک
    mahdieh update [back|front]    به‌روزرسانی یک‌خطی از GitHub (پیش‌فرض: هر دو بخش)
        --check                    فقط نشان بده چه چیزی تازه است؛ چیزی نصب نکن
        --no-wait                  منتظر ساختِ در جریانِ GitHub نمان
        --tag TAG                  نسخهٔ مشخص (پیش‌فرض edge = آخرین نسخهٔ سبزِ main)
        --force                    بازسازی، حتی اگر همین نسخه فعال است
    mahdieh rollback [back|front] [RELEASE]   بازگشت فوری به نسخهٔ قبلی
    mahdieh doctor                 معاینهٔ کامل؛ کد خروج ۰ = سالم · ۱ = ایراد
    mahdieh logs [NAME] [-n N] [-f]           گزارش‌ها (deploy، backup، errors، …)
    mahdieh manage ARGS...         manage.py جنگو در انتشار فعال (مثلاً createsuperuser)
    mahdieh version | help

جریان کار: تغییر در GitHub ⇒ CI (آزمون کامل) ⇒ Release (ساخت بسته + تمرین استقرار روی
همزاد میزبان) ⇒ edge. «mahdieh update» اگر ساختی در جریان باشد صبر می‌کند، فهرست
تغییرها را نشان می‌دهد و همان استقرار اتمیِ ده‌مرحله‌ای (با بازگشت خودکار) را اجرا می‌کند.

فقط کتابخانهٔ استاندارد پایتون ۳.۱۲؛ GitHub بی‌توکن (مخزن عمومی). هر دادهٔ «اختیاری»
(فهرست تغییرها، وضعیت ساخت) اگر در دسترس نباشد، کار اصلی را متوقف نمی‌کند.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import time
from typing import Any, TextIO
import urllib.error
import urllib.request

VERSION = "1.1.0"

ENV = os.environ
HOME = Path(ENV.get("HOME") or Path.home())
BACK_HOME = Path(ENV.get("MAHDIEH_HOME") or HOME / "mahdieh")
FRONT_HOME = Path(ENV.get("MAHDIEH_FRONT_HOME") or HOME / "mahdieh-front")
ENV_SOURCE = Path(ENV.get("MAHDIEH_ENV_SOURCE") or HOME / ".mahdieh" / "mahdieh.env")
GITHUB_WEB = ENV.get("MAHDIEH_GITHUB_WEB", "https://github.com").rstrip("/")
GITHUB_API = ENV.get("MAHDIEH_GITHUB_API", "https://api.github.com").rstrip("/")
VERIFY_BASE_URL = ENV.get("MAHDIEH_VERIFY_BASE_URL", "").rstrip("/")
OFFLINE = ENV.get("MAHDIEH_OFFLINE", "") == "1"
WAIT_SECONDS = int(ENV.get("MAHDIEH_WAIT_SECONDS", "1800"))
POLL_SECONDS = float(ENV.get("MAHDIEH_POLL_SECONDS", "15"))
API_EVERY_SECONDS = float(ENV.get("MAHDIEH_API_EVERY_SECONDS", "60"))
USER_AGENT = f"mahdieh-cli/{VERSION}"

BACKUP_WARN_HOURS = 36
SSL_WARN_DAYS = 14
SSL_FAIL_DAYS = 3
DISK_WARN = 0.80
DISK_FAIL = 0.95
HTTP_OK = 200
REDIRECTS = (301, 308)

# ── نمایش ────────────────────────────────────────────────────────────────────

_FA_DIGITS = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")


def fa(value: object) -> str:
    """ارقام فارسی برای متن انسانی (شناسه‌ها و مسیرها لاتین می‌مانند)."""
    return str(value).translate(_FA_DIGITS)


def fa_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:  # noqa: PLR2004
        return f"{fa(seconds)} ثانیه"
    if seconds < 3600:  # noqa: PLR2004
        minutes, rest = divmod(seconds, 60)
        return f"{fa(minutes)} دقیقه" + (f" و {fa(rest)} ثانیه" if rest and minutes < 10 else "")  # noqa: PLR2004
    if seconds < 86400:  # noqa: PLR2004
        return f"{fa(seconds // 3600)} ساعت"
    return f"{fa(seconds // 86400)} روز"


def fa_ago(seconds: float) -> str:
    return "همین حالا" if seconds < 30 else f"{fa_duration(seconds)} پیش"  # noqa: PLR2004


def fa_clock(seconds: float) -> str:
    minutes, rest = divmod(max(0, int(seconds)), 60)
    return fa(f"{minutes:02d}:{rest:02d}")


def fa_size(num_bytes: float) -> str:
    units = ("بایت", "کیلوبایت", "مگابایت", "گیگابایت")
    size, unit = float(num_bytes), 0
    while size >= 1024 and unit < len(units) - 1:  # noqa: PLR2004
        size /= 1024
        unit += 1
    return f"{fa(f'{size:.0f}' if unit == 0 else f'{size:.1f}')} {units[unit]}"


class Ui:
    """خروجی رنگی و خوانا؛ بی‌رنگ اگر خروجی ترمینال نیست یا NO_COLOR تنظیم شده."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout
        self.tty = self.stream.isatty()
        self.color = self.tty and "NO_COLOR" not in ENV
        self._progress = False

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def write(self, text: str = "") -> None:
        self.end_progress()
        self.stream.write(text + "\n")
        self.stream.flush()

    def banner(self, text: str) -> None:
        rule = "━" * 60
        self.write()
        self.write(self._c("1;36", rule))
        self.write(self._c("1;36", f"  ◆ {text}"))
        self.write(self._c("1;36", rule))

    def section(self, text: str) -> None:
        self.write()
        self.write(self._c("1;35", f"▌{text}"))

    def ok(self, text: str) -> None:
        self.write(f"  {self._c('1;32', '✓')} {text}")

    def warn(self, text: str) -> None:
        self.write(f"  {self._c('1;33', '!')} {text}")

    def bad(self, text: str) -> None:
        self.write(f"  {self._c('1;31', '✗')} {text}")

    def info(self, text: str) -> None:
        self.write(f"  {self._c('0;36', '•')} {text}")

    def dim(self, text: str) -> None:
        self.write(f"    {self._c('0;90', text)}")

    def item(self, text: str) -> None:
        self.write(f"    {self._c('0;90', '◦')} {text}")

    def kv(self, label: str, value: str) -> None:
        self.write(f"  {self._c('1;37', label)}  {value}")

    def progress(self, text: str) -> None:
        if not self.tty:
            return
        self.stream.write("\r\033[2K  " + self._c("0;36", text))
        self.stream.flush()
        self._progress = True

    def end_progress(self) -> None:
        if self._progress:
            self.stream.write("\r\033[2K")
            self.stream.flush()
            self._progress = False


# ── HTTP ─────────────────────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


_FOLLOW = urllib.request.build_opener()
_NO_FOLLOW = urllib.request.build_opener(_NoRedirect)


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return None


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
    follow: bool = True,
) -> Response:
    """هرگز استثنا نمی‌دهد: خطای شبکه = وضعیت ۰."""
    if not url.startswith(("https://", "http://")):
        return Response(0)
    request = urllib.request.Request(  # noqa: S310 — طرح نشانی بالا محدود شد
        url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache", **(headers or {})}
    )
    opener = _FOLLOW if follow else _NO_FOLLOW
    try:
        with opener.open(request, timeout=timeout) as reply:
            return Response(reply.status, reply.read(), dict(reply.headers.items()))
    except urllib.error.HTTPError as error:
        body = error.read() if error.fp else b""
        return Response(error.code, body, dict(error.headers.items()) if error.headers else {})
    except (OSError, ValueError):
        return Response(0)


# ── GitHub (بی‌توکن؛ هر شکست = «نامعلوم»، نه توقف) ───────────────────────────


class GitHub:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.limited = False

    def asset(self, tag: str, name: str) -> Response:
        return http_get(f"{GITHUB_WEB}/{self.repo}/releases/download/{tag}/{name}", timeout=30)

    def build_info(self, tag: str) -> dict[str, Any] | None:
        if OFFLINE:
            return None
        data = self.asset(tag, "BUILD_INFO.json").json()
        return data if isinstance(data, dict) and data.get("release") else None

    def api(self, path: str) -> Any:
        if OFFLINE or self.limited:
            return None
        reply = http_get(
            f"{GITHUB_API}/repos/{self.repo}/{path}",
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        if reply.status in {403, 429} and reply.headers.get("X-RateLimit-Remaining", "") == "0":
            self.limited = True
        return reply.json() if reply.status == HTTP_OK else None

    def head(self, branch: str = "main") -> str | None:
        data = self.api(f"commits/{branch}")
        return data.get("sha") if isinstance(data, dict) else None

    def runs(self, sha: str) -> dict[str, tuple[str, str]]:
        """آخرین اجرای هر گردش‌کار برای کامیت: نام ⇒ (وضعیت، نتیجه)."""
        data = self.api(f"actions/runs?head_sha={sha}&per_page=30")
        found: dict[str, tuple[str, str]] = {}
        for run in (data or {}).get("workflow_runs", []) if isinstance(data, dict) else []:
            found.setdefault(
                str(run.get("name")), (run.get("status") or "", run.get("conclusion") or "")
            )
        return found

    def compare(self, base: str, head: str) -> tuple[int, list[str]] | None:
        data = self.api(f"compare/{base}...{head}")
        if not isinstance(data, dict) or "commits" not in data:
            return None
        subjects = [
            str(c.get("commit", {}).get("message", "")).splitlines()[0]
            for c in data["commits"]
            if c.get("commit", {}).get("message")
        ]
        return int(data.get("total_commits", len(subjects))), subjects[::-1]


# ── بخش‌ها (بک‌اند / فرانت) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Component:
    key: str
    title: str
    repo: str
    home: Path

    @property
    def current(self) -> Path:
        return self.home / "current"

    @property
    def deploy(self) -> Path:
        return self.home / "bin" / "deploy.sh"

    @property
    def releases(self) -> Path:
        return self.home / "releases"

    def installed(self) -> bool:
        return self.current.is_symlink() and self.deploy.is_file()

    def installable(self) -> bool:
        """ابزار استقرار هست ولی هنوز انتشاری فعال نشده (نصب نخست فرانت)."""
        return self.deploy.is_file() and not self.current.is_symlink()

    def build_info(self) -> dict[str, Any]:
        try:
            data = json.loads((self.current / "BUILD_INFO.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def current_release(self) -> str:
        return self.current.resolve().name if self.current.is_symlink() else ""


BACK = Component("back", "بک‌اند (جنگو · API · پنل)", "mohebbibrothers/Mahdieh", BACK_HOME)
FRONT = Component("front", "فرانت (Next.js)", "mohebbibrothers/Mahdieh-Front", FRONT_HOME)
COMPONENTS = {"back": BACK, "front": FRONT}
FRONT_PENDING = (
    "ابزار استقرار فرانت هنوز نصب نیست؛ نخست بک‌اند را به‌روز کنید (mahdieh update back) "
    "تا ‎~/mahdieh-front/bin/deploy.sh نصب شود، سپس: mahdieh update front"
)


def is_newer(candidate: object, active: object) -> bool:
    """شناسهٔ انتشار «YYYYMMDDHHMMSS-کامیت» است؛ مقایسهٔ رشته‌ای = مقایسهٔ زمان ساخت."""
    return str(candidate or "") > str(active or "")


def short(commit: object) -> str:
    return str(commit or "")[:7] or "—"


def built_ago(info: dict[str, Any]) -> str:
    try:
        built = datetime.strptime(str(info.get("built_at")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return ""
    return fa_ago(time.time() - built.timestamp())


# ── سایت ─────────────────────────────────────────────────────────────────────


def env_value(key: str, path: Path = ENV_SOURCE) -> str:
    """خواندن یک کلید از فایل محیط بی‌آنکه source شود (مقادیر فارسیِ بی‌نقل‌قول)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        match = re.match(rf"\s*{re.escape(key)}\s*=\s*(.*)$", line)
        if match:
            value = match.group(1).strip().strip("'\"")
    return value


class Site:
    def __init__(self) -> None:
        self.url = env_value("MAHDIEH_SITE_URL").rstrip("/")
        self.domain = self.url.removeprefix("https://")

    @property
    def configured(self) -> bool:
        return bool(re.fullmatch(r"https://[A-Za-z0-9.-]+", self.url))

    def get(self, path: str, *, follow: bool = False) -> Response:
        if VERIFY_BASE_URL:
            return http_get(
                VERIFY_BASE_URL + path,
                headers={"Host": self.domain, "X-Forwarded-Proto": "https"},
                follow=follow,
            )
        return http_get(self.url + path, follow=follow)

    def health(self, kind: str) -> dict[str, Any] | None:
        reply = self.get(f"/health/{kind}/")
        data = reply.json() if reply.status == HTTP_OK else None
        return data if isinstance(data, dict) else None

    def http_redirect(self) -> Response:
        return http_get(f"http://{self.domain}/health/live/", follow=False)

    def ssl_days_left(self) -> float | None:
        try:
            context = ssl.create_default_context()
            with (
                socket.create_connection((self.domain, 443), timeout=10) as raw,
                context.wrap_socket(raw, server_hostname=self.domain) as tls,
            ):
                cert = tls.getpeercert()
        except (OSError, ssl.SSLError):
            return None
        not_after = cert.get("notAfter") if cert else None
        if not isinstance(not_after, str):
            return None
        return (ssl.cert_time_to_seconds(not_after) - time.time()) / 86400


# ── ابزار پوسته ──────────────────────────────────────────────────────────────


def run_capture(args: list[str], timeout: float = 30) -> str | None:
    if not shutil.which(args[0]):
        return None
    try:
        done = subprocess.run(  # noqa: S603 — آرگومان‌ها ثابت و درون‌کدی‌اند
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def uapi(*args: str) -> Any:
    out = run_capture(["uapi", "--output=json", *args], timeout=60)
    if out is None:
        return None
    try:
        result = json.loads(out).get("result", {})
    except (ValueError, AttributeError):
        return None
    return result.get("data") if result.get("status") == 1 else None


def crontab_jobs() -> int | None:
    out = run_capture(["crontab", "-l"])
    if out is None:
        return None
    return sum(
        1
        for line in out.splitlines()
        if "cron_run.sh" in line and not line.lstrip().startswith("#")
    )


def newest_file(directory: Path, pattern: str = "*") -> Path | None:
    try:
        files = [p for p in directory.rglob(pattern) if p.is_file()]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


# ── update ───────────────────────────────────────────────────────────────────

_RUN_STATE = {
    "queued": "در صف",
    "in_progress": "در جریان",
    "waiting": "در انتظار",
    "pending": "در صف",
}
_FAILED = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
CI_WORKFLOW = "CI"
RELEASE_WORKFLOW = "Release bundle"


def run_label(state: tuple[str, str] | None) -> str:
    if state is None:
        return "—"
    status, conclusion = state
    if status == "completed":
        return "✓" if conclusion == "success" else f"✗ {conclusion}"
    return _RUN_STATE.get(status, status or "—")


def failed_run(runs: dict[str, tuple[str, str]]) -> str | None:
    for name in (CI_WORKFLOW, RELEASE_WORKFLOW):
        state = runs.get(name)
        if state and state[0] == "completed" and state[1] in _FAILED:
            return f"{name}: {state[1]}"
    return None


def await_fresh_build(
    gh: GitHub,
    tag: str,
    remote: dict[str, Any] | None,
    ui: Ui,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """اگر آخرین کامیت main هنوز منتشر نشده، تا پایان آزمون و ساختِ GitHub صبر کن."""
    head = gh.head()
    if head is None:
        if gh.limited:
            ui.dim("سقف درخواست بی‌توکنِ GitHub برای این IP پر است؛ آخرین نسخهٔ منتشرشده معیار است.")
        return remote
    if remote and remote.get("commit") == head:
        return remote
    started = clock()
    last_api = -float("inf")
    runs: dict[str, tuple[str, str]] = {}
    ui.info(
        f"تغییر تازه‌ای ({short(head)}) در GitHub هست که هنوز منتشر نشده؛ صبر می‌کنم تا آزموده و ساخته شود…"
    )
    while clock() - started < WAIT_SECONDS:
        if clock() - last_api >= API_EVERY_SECONDS:
            last_api = clock()
            head = gh.head() or head
            runs = gh.runs(head)
            failure = failed_run(runs)
            if failure:
                ui.warn(
                    f"آخرین تغییر ({short(head)}) در GitHub رد شد ({failure}) — منتشر نمی‌شود؛ "
                    "آخرین نسخهٔ سبز معیار است."
                )
                return gh.build_info(tag) or remote
            if not runs and clock() - started >= 90:  # noqa: PLR2004
                ui.dim("برای آخرین تغییر ساختی در کار نیست؛ آخرین نسخهٔ منتشرشده معیار است.")
                return gh.build_info(tag) or remote
        fresh = gh.build_info(tag)
        if fresh and fresh.get("commit") == head:
            ui.ok(
                f"نسخهٔ تازه در GitHub آزموده، تمرین و منتشر شد (انتظار {fa_clock(clock() - started)})"
            )
            return fresh
        ui.progress(
            f"⏳ آزمون: {run_label(runs.get(CI_WORKFLOW))} · ساخت و تمرین: "
            f"{run_label(runs.get(RELEASE_WORKFLOW))} · {fa_clock(clock() - started)}"
        )
        sleep(POLL_SECONDS)
    ui.warn(
        f"پس از {fa_duration(WAIT_SECONDS)} نسخهٔ تازه آماده نشد؛ آخرین نسخهٔ منتشرشده معیار است."
    )
    return gh.build_info(tag) or remote


def show_changes(gh: GitHub, local: dict[str, Any], remote: dict[str, Any], ui: Ui) -> None:
    base, head = str(local.get("commit") or ""), str(remote.get("commit") or "")
    if not base or not head:
        return
    changes = gh.compare(base, head)
    if changes is None:
        ui.dim(f"تغییرها: {GITHUB_WEB}/{gh.repo}/compare/{short(base)}...{short(head)}")
        return
    total, subjects = changes
    ui.info(f"{fa(total)} تغییر تازه:")
    for subject in subjects[:12]:
        ui.item(subject)
    if total > len(subjects[:12]):
        ui.dim(f"… و {fa(total - len(subjects[:12]))} تغییر دیگر")


def already_current(active: str, release: str, args: argparse.Namespace) -> str | None:
    """پیام «لازم نیست» یا None؛ edge هرگز به نسخهٔ قدیمی‌تر برنمی‌گرداند (برچسب صریح چرا)."""
    if args.force:
        return None
    if release == active:
        return f"به‌روز است · {release}"
    if args.tag == "edge" and not is_newer(release, active):
        return f"نسخهٔ فعال از آخرین نسخهٔ منتشرشده ({release}) تازه‌تر است؛ تغییری لازم نیست."
    return None


def update_component(comp: Component, args: argparse.Namespace, ui: Ui) -> tuple[str, str]:
    """(نتیجه، توضیح) — نتیجه یکی از: updated, current, checked, skipped, failed."""
    ui.section(comp.title)
    first_install = comp.key == "front" and comp.installable()
    if not comp.installed() and not first_install:
        if comp.key == "front":
            ui.info(FRONT_PENDING)
            return "skipped", "هنوز راه‌اندازی نشده"
        ui.bad(f"نصب نیست ({comp.current} یا {comp.deploy} پیدا نشد).")
        return "failed", "نصب نیست"
    local = {} if first_install else comp.build_info()
    if first_install:
        ui.info("نصب نخست: هنوز انتشاری فعال نیست؛ آخرین نسخهٔ سبز نصب می‌شود.")
    else:
        ui.kv("فعال:", f"{local.get('release', comp.current_release())} · {built_ago(local)}")
    gh = GitHub(comp.repo)
    remote = gh.build_info(args.tag)
    if args.tag == "edge" and not args.no_wait:
        remote = await_fresh_build(gh, args.tag, remote, ui)
    if remote is None:
        ui.bad(f"دریافت مشخصات نسخهٔ «{args.tag}» از GitHub نشد (شبکه یا برچسب نادرست).")
        return "failed", "GitHub در دسترس نبود"
    release = str(remote["release"])
    note = already_current(str(local.get("release") or ""), release, args)
    if note:
        ui.ok(note)
        return "current", str(local.get("release"))
    ui.kv("تازه:", f"{release} · {built_ago(remote)}")
    show_changes(gh, local, remote, ui)
    if args.check:
        ui.info("برای نصب: mahdieh update")
        return "checked", release
    return run_deploy(comp, args, release, ui)


def run_deploy(comp: Component, args: argparse.Namespace, release: str, ui: Ui) -> tuple[str, str]:
    command = ["bash", str(comp.deploy), "fetch", "--tag", args.tag]
    if args.force or (comp.releases / release).is_dir():
        command.append(
            "--force"
        )  # پوشهٔ مانده از بازگشت قبلی؛ deploy.sh انتشار فعال را بازنویسی نمی‌کند
    ui.write()
    started = time.monotonic()
    code = subprocess.call(command)  # noqa: S603 — مسیر deploy.sh ثابت است
    took = fa_duration(time.monotonic() - started)
    if code == 0:
        ui.ok(f"{comp.title} روی {release} است ({took})")
        return "updated", release
    ui.bad(f"استقرار با کد {fa(code)} متوقف شد ({took}). سایت روی نسخهٔ قبلی سالم مانده است.")
    ui.dim("جزئیات: mahdieh logs deploy")
    return "failed", f"کد {code}"


def _cli_fingerprint() -> str:
    """امضای فرمانِ نصب‌شده؛ تغییرش یعنی استقرار بک‌اند نسخهٔ تازهٔ همین فرمان را گذاشته است."""
    installed = BACK_HOME / "bin" / "mahdieh_cli.py"
    try:
        info = installed.stat()
    except OSError:
        return ""
    return f"{info.st_size}:{info.st_mtime_ns}"


def handoff_argv(args: argparse.Namespace, target: str) -> list[str]:
    """آرگومان‌های اجرای دوبارهٔ «update» برای بخش باقی‌مانده با فرمان تازه."""
    argv = ["update", target, "--tag", args.tag]
    argv += ["--check"] if args.check else []
    argv += ["--no-wait"] if args.no_wait else []
    argv += ["--force"] if args.force else []
    return argv


def cmd_update(args: argparse.Namespace, ui: Ui) -> int:
    targets = [COMPONENTS[args.target]] if args.target != "all" else [BACK, FRONT]
    ui.banner(f"مهدیه · به‌روزرسانی {'(فقط بررسی)' if args.check else ''}".strip())
    before = _cli_fingerprint()
    results = []
    for comp in targets:
        if (
            comp is FRONT
            and results
            and results[-1][1] == "updated"
            and _cli_fingerprint() != before
        ):
            # بک‌اند فرمان تازه‌ای نصب کرد (شاید با منطق تازهٔ فرانت) ⇒ ادامه با همان فرمان تازه.
            ui.info("فرمان mahdieh همراه بک‌اند به‌روز شد؛ ادامهٔ کار با نسخهٔ تازه…")
            installed = BACK_HOME / "bin" / "mahdieh_cli.py"
            os.execv(sys.executable, [sys.executable, str(installed), *handoff_argv(args, "front")])  # noqa: S606
        results.append((comp, *update_component(comp, args, ui)))
    ui.section("خلاصه")
    labels = {
        "updated": "✅ به‌روز شد",
        "current": "✓ به‌روز بود",
        "checked": "◆ نسخهٔ تازه موجود است",
        "skipped": "○ رد شد",
        "failed": "✗ ناموفق",
    }
    for comp, outcome, detail in results:
        ui.kv(f"{comp.title}:", f"{labels[outcome]} · {detail}")
    site = Site()
    if site.configured and any(outcome == "updated" for _, outcome, _ in results):
        ui.kv("نشانی:", site.url)
    return 1 if any(outcome == "failed" for _, outcome, _ in results) else 0


# ── doctor / status ──────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    level: str  # ok · warn · fail · skip
    detail: str


def _mode(path: Path) -> int | None:
    try:
        return path.stat().st_mode
    except OSError:
        return None


def check_release(comp: Component) -> Check:
    if not comp.installed():
        return Check("انتشار فعال", "fail", "current یا ابزار استقرار نیست")
    info = comp.build_info()
    if comp.key == "front":
        if (
            info.get("release") != comp.current_release()
            or not (comp.current / "routes.json").is_file()
        ):
            return Check("انتشار فعال", "fail", f"بستهٔ فرانت {comp.current_release()} ناقص است")
    elif not (comp.current / ".venv" / ".complete").exists():
        return Check("انتشار فعال", "fail", f"محیط مجازی {comp.current_release()} ناقص است")
    return Check(
        "انتشار فعال",
        "ok",
        f"{comp.current_release()} · کامیت {short(info.get('commit'))} · {built_ago(info)}",
    )


def check_permissions(home: Path = BACK_HOME) -> Check:
    app = home / "app"
    needs_x = [HOME, home, app, app / "tmp"]
    blocked = [str(p) for p in needs_x if (m := _mode(p)) is None or not m & stat.S_IXOTH]
    wsgi = _mode(app / "passenger_wsgi.py")
    if wsgi is None or not wsgi & stat.S_IROTH:
        blocked.append(str(app / "passenger_wsgi.py") + " (خواندن)")
    if blocked:
        return Check("دسترسی وب‌سرور به برنامه", "fail", "بسته: " + "، ".join(blocked))
    return Check("دسترسی وب‌سرور به برنامه", "ok", "ریشهٔ Passenger قابل عبور (۷۱۱ · ۶۴۴)")


def check_env_file() -> Check:
    mode = _mode(ENV_SOURCE)
    if mode is None:
        return Check("فایل رازها", "fail", f"{ENV_SOURCE} نیست")
    if mode & 0o077:
        return Check("فایل رازها", "fail", f"دسترسی {oct(mode & 0o777)} — باید ۶۰۰ باشد")
    return Check("فایل رازها", "ok", "فقط برای حساب شما (۶۰۰)")


def check_live(site: Site, comp: Component) -> Check:
    live = site.health("live")
    if live is None:
        return Check("سایت زنده", "fail", f"{site.url}/health/live/ پاسخ سالم نداد")
    seen, expected = live.get("release"), comp.current_release()
    if seen != expected:
        return Check("سایت زنده", "warn", f"دامنه {seen} را سرو می‌کند، فعال {expected} است")
    return Check("سایت زنده", "ok", "دامنه همان انتشار فعال را سرو می‌کند")


def check_ready(site: Site) -> Check:
    ready = site.health("ready")
    if ready is None:
        return Check("آمادگی", "fail", "/health/ready/ پاسخ سالم نداد")
    checks = ready.get("checks", {}) if isinstance(ready.get("checks"), dict) else {}
    db = checks.get("database", {})
    pending = checks.get("migrations", {}).get("pending", "?")
    detail = f"پایگاه‌داده {fa(db.get('latency_ms', '?'))}ms · مهاجرت معلق {fa(pending)}"
    if ready.get("status") != "healthy":
        failed = "، ".join(ready.get("failed") or []) or ready.get("status")
        return Check("آمادگی", "fail", f"{failed} · {detail}")
    return Check("آمادگی", "ok", detail)


def check_https_redirect(site: Site) -> Check:
    if VERIFY_BASE_URL:
        return Check("HTTPS اجباری", "skip", "حالت آزمون")
    reply = site.http_redirect()
    location = reply.headers.get("Location", "")
    if reply.status in REDIRECTS and location.startswith("https://"):
        return Check("HTTPS اجباری", "ok", "HTTP به HTTPS هدایت می‌شود")
    return Check("HTTPS اجباری", "warn", f"HTTP پاسخ {fa(reply.status)} داد")


def check_ssl(site: Site) -> Check:
    if VERIFY_BASE_URL:
        return Check("گواهی SSL", "skip", "حالت آزمون")
    days = site.ssl_days_left()
    if days is None:
        return Check("گواهی SSL", "fail", "گواهی خوانده نشد")
    level = "fail" if days < SSL_FAIL_DAYS else "warn" if days < SSL_WARN_DAYS else "ok"
    return Check("گواهی SSL", level, f"{fa(int(days))} روز اعتبار (تمدید خودکار cPanel)")


def check_crontab(comp: Component) -> Check:
    jobs = crontab_jobs()
    try:
        template = (comp.current / "deploy" / "cpanel" / "crontab.tmpl").read_text(encoding="utf-8")
        expected = sum(1 for line in template.splitlines() if re.match(r"^[0-9*]", line))
    except OSError:
        expected = 0
    if jobs is None:
        return Check("کارهای زمان‌بندی", "fail", "crontab خوانده نشد")
    if expected and jobs < expected:
        return Check("کارهای زمان‌بندی", "fail", f"{fa(jobs)} از {fa(expected)} کار نصب است")
    return Check("کارهای زمان‌بندی", "ok", f"{fa(jobs)} کار فعال")


def check_job_errors(home: Path = BACK_HOME) -> Check:
    log_dir = home / "shared" / "var" / "log"
    since = time.time() - 86400
    failing: set[str] = set()
    for log in sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []:
        if log.name == "deploy.log":
            continue
        try:
            if log.stat().st_mtime < since:
                continue
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            continue
        for line in tail:
            if "✗ کد خروج" not in line:
                continue
            try:
                when = datetime.fromisoformat(line.split(" ", 1)[0]).timestamp()
            except ValueError:
                continue
            if when >= since:
                failing.add(log.stem)
    if failing:
        names = "، ".join(sorted(failing))
        return Check("خطای کارها (۲۴ ساعت)", "warn", f"{names} — mahdieh logs NAME")
    return Check("خطای کارها (۲۴ ساعت)", "ok", "بدون خطا")


def check_backup(home: Path = BACK_HOME) -> Check:
    """آخرین **خود پشتیبان** (``*.sql.gz``)، نه هر فایلی در پوشه.

    پیش‌تر جدیدترین فایل هر نوعی بود؛ بیانیهٔ ``.sql.gz.json`` (۱٫۶KB) که بلافاصله
    پس از پشتیبان نوشته می‌شود، به‌جای خود پشتیبان (۱۸KB) گزارش می‌شد — و اگر
    پشتیبان پاک یا خراب می‌شد و فقط بیانیه می‌ماند، doctor باز «سبز» می‌گفت.
    پشتیبانِ بی‌بیانیه هم هشدار است: بازیابی تأییدشده (``--expect-sha256``) به آن تکیه دارد.
    """
    latest = newest_file(home / "shared" / "var" / "backups", "*.sql.gz")
    if latest is None:
        return Check("آخرین پشتیبان", "warn", "هنوز پشتیبانی نیست (پشتیبان شبانه ساعت ۲:۳۰)")
    stat = latest.stat()
    age = time.time() - stat.st_mtime
    detail = f"{fa_ago(age)} · {fa_size(stat.st_size)}"
    if not latest.with_name(f"{latest.name}.json").is_file():
        return Check("آخرین پشتیبان", "warn", f"{detail} · بیانیهٔ اثرانگشت (.json) ندارد")
    level = "warn" if age > BACKUP_WARN_HOURS * 3600 else "ok"
    return Check("آخرین پشتیبان", level, detail)


def check_disk(home: Path = BACK_HOME) -> Check:
    quota = uapi("Quota", "get_quota_info")
    if isinstance(quota, dict):
        try:
            used = float(quota.get("megabytes_used") or 0)
            limit = float(quota.get("megabyte_limit") or 0)
        except (TypeError, ValueError):
            used = limit = 0
        if limit > 0:
            ratio = used / limit
            level = "fail" if ratio >= DISK_FAIL else "warn" if ratio >= DISK_WARN else "ok"
            return Check(
                "فضای دیسک",
                level,
                f"{fa(int(used))} از {fa(int(limit))} مگابایت ({fa(int(ratio * 100))}٪)",
            )
    return Check("فضای دیسک", "ok", f"مهدیه {fa_size(dir_size(home))}")


def check_newer(comp: Component) -> Check:
    remote = GitHub(comp.repo).build_info("edge")
    if remote is None:
        return Check("نسخهٔ تازه", "skip", "GitHub بررسی نشد")
    if remote.get("release") == comp.current_release():
        return Check("نسخهٔ تازه", "ok", "آخرین نسخهٔ منتشرشده نصب است")
    if not is_newer(remote.get("release"), comp.current_release()):
        return Check("نسخهٔ تازه", "ok", f"نسخهٔ فعال از edge ({remote.get('release')}) تازه‌تر است")
    return Check("نسخهٔ تازه", "warn", f"{remote.get('release')} آماده است — mahdieh update")


def check_front() -> Check:
    if not FRONT.installed():
        detail = "ابزار نصب است؛ mahdieh update front" if FRONT.installable() else "هنوز نصب نشده"
        return Check("فرانت", "skip", detail)
    check = check_release(FRONT)
    return Check(f"فرانت · {check.name}", check.level, check.detail)


def check_front_live(site: Site) -> Check:
    """جنگو همان انتشار فرانتی را سرو می‌کند که ‎~/mahdieh-front/current نشان می‌دهد؟"""
    if not FRONT.installed():
        return Check("فرانت زنده", "skip", "فرانت هنوز نصب نشده")
    live = site.health("live")
    if live is None:
        return Check("فرانت زنده", "fail", "/health/live/ پاسخ سالم نداد")
    if "front" not in live:
        return Check(
            "فرانت زنده", "warn", "بک‌اند قدیمی است و فرانت را نمی‌شناسد — mahdieh update back"
        )
    seen, expected = live.get("front"), FRONT.current_release()
    if seen != expected:
        return Check(
            "فرانت زنده", "fail", f"دامنه فرانت «{seen or 'هیچ'}» را سرو می‌کند، فعال {expected} است"
        )
    return Check("فرانت زنده", "ok", "دامنه همان انتشار فعال فرانت را سرو می‌کند")


def render(checks: Iterable[Check], ui: Ui) -> tuple[int, int]:
    fails = warns = 0
    for check in checks:
        line = f"{check.name}: {check.detail}"
        if check.level == "ok":
            ui.ok(line)
        elif check.level == "warn":
            warns += 1
            ui.warn(line)
        elif check.level == "fail":
            fails += 1
            ui.bad(line)
        else:
            ui.info(line)
    return fails, warns


def cmd_doctor(args: argparse.Namespace, ui: Ui) -> int:
    del args
    site = Site()
    ui.banner("مهدیه · معاینهٔ کامل")
    ui.section("استقرار")
    fails, warns = render(
        [
            check_release(BACK),
            check_permissions(),
            check_env_file(),
            check_newer(BACK),
            check_front(),
        ],
        ui,
    )
    ui.section(f"سایت · {site.url or 'نشانی تنظیم نشده'}")
    online = [
        check_live(site, BACK),
        check_front_live(site),
        check_ready(site),
        check_https_redirect(site),
        check_ssl(site),
    ]
    f2, w2 = render(online, ui)
    ui.section("نگهداری")
    f3, w3 = render([check_crontab(BACK), check_job_errors(), check_backup(), check_disk()], ui)
    fails, warns = fails + f2 + f3, warns + w2 + w3
    ui.section("نتیجه")
    if fails:
        ui.bad(f"{fa(fails)} ایراد و {fa(warns)} هشدار")
    elif warns:
        ui.warn(f"سالم، با {fa(warns)} هشدار")
    else:
        ui.ok("همه‌چیز سالم است")
    return 1 if fails else 0


def cmd_status(args: argparse.Namespace, ui: Ui) -> int:
    del args
    site = Site()
    ui.banner("مهدیه · داشبورد")
    ui.section(BACK.title)
    if not BACK.installed():
        ui.bad("نصب نیست")
        return 1
    fails, _ = render([check_release(BACK), check_newer(BACK)], ui)
    ui.section(FRONT.title)
    if FRONT.installed():
        render([check_release(FRONT), check_newer(FRONT)], ui)
    elif FRONT.installable():
        ui.info("ابزار استقرار فرانت نصب است؛ برای نصب نخست: mahdieh update front")
    else:
        ui.info(FRONT_PENDING)
    ui.section(f"سایت · {site.url}")
    f2, _ = render([check_live(site, BACK), check_front_live(site), check_ready(site)], ui)
    ui.section("نگهداری")
    render([check_backup(), check_disk()], ui)
    ui.write()
    ui.dim("معاینهٔ کامل: mahdieh doctor · به‌روزرسانی: mahdieh update · راهنما: mahdieh help")
    return 1 if fails + f2 else 0


# ── logs / rollback / manage ─────────────────────────────────────────────────


def cmd_logs(args: argparse.Namespace, ui: Ui) -> int:  # noqa: RET503 — execv برنمی‌گردد
    log_dir = BACK_HOME / "shared" / "var" / "log"
    if not args.name:
        ui.banner("مهدیه · گزارش‌ها")
        for log in sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []:
            info = log.stat()
            ui.kv(
                f"{log.stem:<12}",
                f"{fa_size(info.st_size)} · {fa_ago(time.time() - info.st_mtime)}",
            )
        ui.kv(f"{'errors':<12}", "خطاهای وب‌سرور برای دامنه (Apache/Passenger)")
        ui.write()
        ui.dim("نمایش: mahdieh logs NAME [-n 80] [-f]   مثلاً: mahdieh logs deploy")
        return 0
    if args.name == "errors":
        site = Site()
        rows = uapi(
            "Stats",
            "get_site_errors",
            f"domain={site.domain}",
            "log=error",
            f"maxlines={args.lines}",
        )
        if rows is None:
            ui.bad("گزارش خطای وب‌سرور در دسترس نیست (uapi).")
            return 1
        for row in reversed(rows if isinstance(rows, list) else []):
            ui.write(str(row.get("entry") if isinstance(row, dict) else row))
        return 0
    if not re.fullmatch(r"[a-z0-9_-]+", args.name):
        ui.bad("نام گزارش نامعتبر است.")
        return 2
    log = log_dir / f"{args.name}.log"
    if not log.is_file():
        ui.bad(f"گزارش «{args.name}» نیست. فهرست: mahdieh logs")
        return 1
    tail = shutil.which("tail") or "tail"
    flags = ["-n", str(args.lines)] + (["-F"] if args.follow else [])
    os.execv(tail, [tail, *flags, str(log)])  # noqa: S606


def cmd_rollback(args: argparse.Namespace, ui: Ui) -> int:  # noqa: RET503 — execv برنمی‌گردد
    comp = COMPONENTS[args.target]
    if not comp.installed():
        ui.info(FRONT_PENDING if comp.key == "front" else "نصب نیست.")
        return 0 if comp.key == "front" else 1
    bash = shutil.which("bash") or "/bin/bash"
    os.execv(bash, [bash, str(comp.deploy), "rollback", *([args.release] if args.release else [])])  # noqa: S606


def cmd_manage(args: argparse.Namespace, ui: Ui) -> int:  # noqa: RET503 — execv برنمی‌گردد
    if not BACK.installed():
        ui.bad("بک‌اند نصب نیست.")
        return 1
    release = BACK.current.resolve()
    python = release / ".venv" / "bin" / "python"
    os.chdir(release / "src")
    os.execv(python, [str(python), "manage.py", *args.args])  # noqa: S606


def cmd_version(args: argparse.Namespace, ui: Ui) -> int:
    del args
    info = BACK.build_info()
    ui.write(
        f"mahdieh {VERSION} · بک‌اند {info.get('release', '—')} · فرانت {FRONT.current_release() or '—'}"
    )
    return 0


def cmd_help(args: argparse.Namespace, ui: Ui) -> int:
    del args
    ui.write((__doc__ or "").split("\n\nفقط کتابخانه")[0])
    return 0


# ── ورودی ────────────────────────────────────────────────────────────────────


class UsageError(Exception):
    """خطای ورودی فرمان؛ پیام فارسی به‌جای usage انگلیسی argparse."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="mahdieh", add_help=False)
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    update = sub.add_parser("update", add_help=False)
    update.add_argument("target", nargs="?", choices=["all", "back", "front"], default="all")
    update.add_argument("--check", action="store_true")
    update.add_argument("--no-wait", action="store_true")
    update.add_argument("--force", action="store_true")
    update.add_argument("--tag", default="edge", type=_tag)
    rollback = sub.add_parser("rollback", add_help=False)
    rollback.add_argument("target", nargs="?", choices=["back", "front"], default="back")
    rollback.add_argument("release", nargs="?", type=_tag)
    logs = sub.add_parser("logs", add_help=False)
    logs.add_argument("name", nargs="?")
    logs.add_argument("-n", "--lines", type=int, default=60)
    logs.add_argument("-f", "--follow", action="store_true")
    manage = sub.add_parser("manage", add_help=False)
    manage.add_argument("args", nargs=argparse.REMAINDER)
    for name in ("status", "doctor", "version", "help"):
        sub.add_parser(name, add_help=False)
    return parser


def _tag(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise argparse.ArgumentTypeError(f"نامعتبر: {value}")
    return value


HANDLERS: dict[str, Callable[[argparse.Namespace, Ui], int]] = {
    "update": cmd_update,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "logs": cmd_logs,
    "rollback": cmd_rollback,
    "manage": cmd_manage,
    "version": cmd_version,
    "help": cmd_help,
}


def main(argv: list[str] | None = None, ui: Ui | None = None) -> int:
    ui = ui or Ui()
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in {"-h", "--help"}:
        raw = ["help"]
    try:
        args = build_parser().parse_args(raw)
    except UsageError:
        ui.bad(f"فرمان یا گزینهٔ نامعتبر: «{' '.join(raw)}» — راهنما: mahdieh help")
        return 2
    try:
        return HANDLERS[args.command or "status"](args, ui)
    except KeyboardInterrupt:
        ui.write()
        ui.warn(
            "متوقف شد (Ctrl+C). اگر استقرار وسط کار بود، تا پیش از «جابه‌جایی» سایت دست‌نخورده است."
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
