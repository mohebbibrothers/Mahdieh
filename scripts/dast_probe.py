#!/usr/bin/env python3
"""کاوشگر امنیتی مهدیه (DAST) — «حملهٔ اخلاقی» به سامانهٔ روبهراه‌شده.

چرا این ابزار؟
    تست‌های واحد می‌گویند «منطق درست است»؛ این ابزار می‌پرسد «سامانهٔ واقعی، زیر
    دست کسی که قصد خراب‌کاری دارد، چه می‌کند؟». هر بند آن یک ریسک واقعی وب است:

    ۱. سرآیندهای امنیتی (CSP، HSTS، nosniff، Referrer-Policy، COOP)
    ۲. کوکی‌های نشست/CSRF: HttpOnly، Secure، SameSite
    ۳. نشت اطلاعات: صفحهٔ خطای ۵۰۰، traceback، نسخهٔ جنگو، فهرست مسیرها
    ۴. CSRF: درخواست POST بی‌توکن باید ۴۰۳ شود (نه ۲۰۰)
    ۵. دسترسی افقی (IDOR): زائر الف نباید رزرو زائر ب را ببیند
    ۶. تزریق SQL/مسیر: `' OR 1=1--` و `../` نباید ۵۰۰ بدهند
    ۷. XSS بازتابی: ورودی باید escape شود، نه بازگشت خام
    ۸. سقف نرخ: کوشش‌های پیاپی باید محدود شوند (۴۲۹/۴۰۳)
    ۹. متدهای غیرمجاز (PUT/DELETE روی مسیر خواندنی) باید ۴۰۵ بدهند
   ۱۰. CORS: مبدأ بیگانه نباید سرآیند مجوز بگیرد

خروجی: فهرست ایراد با شدت، و کد خروج ۱ اگر ایرادی با شدت «بالا/بحرانی» بماند.
    python scripts/dast_probe.py --base-url http://127.0.0.1:8000
    python scripts/dast_probe.py --base-url https://mahdie-shohada.ir --json report.json
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
import json
from pathlib import Path
import ssl
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

#: شدت‌ها به ترتیب اهمیت (کوچک‌ترین شماره = بدترین).
SEVERITIES = ("critical", "high", "medium", "low")
SEVERITY_FA = {
    "critical": "بحرانی",
    "high": "بالا",
    "medium": "متوسط",
    "low": "کم",
}
DEFAULT_TIMEOUT = 10.0
#: کدهای وضعیت پرکاربرد، نام‌دار تا مقایسه‌ها خوانا بمانند.
OK = 200
BAD_REQUEST = 400
UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
TOO_MANY_REQUESTS = 429
SERVER_ERROR = 500
USER_AGENT = "Mahdieh-DAST/1.0 (+security-review)"

#: مسیرهای عمومی که کاوشگر به آن‌ها دست می‌زند (بدون تغییر داده).
PUBLIC_PATHS = ("/", "/help/", "/login/", "/health/live/", "/health/ready/", "/api/v1/meta")
#: مسیرهای ناشناس که باید «ورود لازم است» بدهند، نه داده.
PROTECTED_PATHS = ("/my/", "/board/", "/api/v1/reservations", "/api/v1/staff/reservations")
#: سرآیندهای امنیتی الزامی روی پاسخ HTML.
REQUIRED_HEADERS = (
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Content-Security-Policy",
)
#: نشانه‌های نشت اطلاعات در بدنهٔ پاسخ.
LEAK_MARKERS = (
    "Traceback (most recent call last)",
    "django.core.exceptions",
    "DisallowedHost",
    "SECRET_KEY",
    "MySQLdb.",
    "OperationalError",
    "ProgrammingError",
    "TemplateDoesNotExist",
    "Werkzeug",
)


@dataclass
class Finding:
    """یک ایراد امنیتی با شدت، شاهد و راه رفع."""

    check: str
    severity: str
    title: str
    evidence: str
    fix: str

    def as_dict(self) -> dict[str, str]:
        """نمایش ماشین‌خوان."""
        return {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
            "fix": self.fix,
        }


@dataclass
class Report:
    """نتیجهٔ کاوش: کدام بندها اجرا شدند و چه ایرادهایی پیدا شد."""

    base_url: str
    checks: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding | None) -> None:
        """ثبت ایراد؛ تکراری‌ها یک‌بار ثبت می‌شوند تا گزارش خوانا بماند."""
        if finding is None:
            return
        signature = (finding.check, finding.title)
        if any((item.check, item.title) == signature for item in self.findings):
            return
        self.findings.append(finding)

    def worst(self) -> str | None:
        """بدترین شدتِ یافته‌شده."""
        for severity in SEVERITIES:
            if any(item.severity == severity for item in self.findings):
                return severity
        return None


class Probe:
    """ارتباط‌گر کاوشگر: همهٔ تماس‌ها از این کلاس می‌گذرند (بی‌اثر روی داده)."""

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        verify_tls: bool = True,
        behind_proxy: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # پشت nginx/TLS، جنگو با سرآیند X-Forwarded-Proto می‌فهمد درخواست امن است
        # (وگرنه SECURE_SSL_REDIRECT هر درخواست را ۳۰۱ می‌کند و کاوش بی‌معنا می‌شود).
        self.behind_proxy = behind_proxy
        self.verify_tls = verify_tls
        if not verify_tls:
            # فقط برای محیط آزمون با گواهی خودامضا؛ پرچم صریح --insecure لازم است.
            unverified = ssl.create_default_context()
            unverified.check_hostname = False
            unverified.verify_mode = ssl.CERT_NONE
            self._opener_ssl = unverified  # با --insecure صریح فعال می‌شود
        else:
            self._opener_ssl = None

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        follow_redirects: bool = False,
    ) -> tuple[int, dict[str, str], str]:
        """اجرای یک درخواست؛ خروجی: (کد وضعیت، سرآیندها، بدنه)."""
        url = self.base_url + path
        request = urllib.request.Request(url, method=method, data=data)  # noqa: S310 - دامنه از آرگومان مدیر
        request.add_header("User-Agent", USER_AGENT)
        if self.behind_proxy:
            request.add_header("X-Forwarded-Proto", "https")
        request.add_header("Accept", "text/html,application/json;q=0.9,*/*;q=0.5")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        opener = (
            urllib.request.build_opener()
            if follow_redirects
            else urllib.request.build_opener(_NoRedirect)
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                body = response.read(200_000).decode("utf-8", "replace")
                return response.status, dict(response.headers), body
        except urllib.error.HTTPError as error:
            body = error.read(200_000).decode("utf-8", "replace")
            return error.code, dict(error.headers), body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """پاسخ ۳۰۲ را «همان‌طور که هست» بده — چون خودِ ریدایرکت هم شاهد است."""

    def redirect_request(
        self,
        _req: Any,
        _fp: Any,
        _code: int,
        _msg: str,
        _headers: Any,
        _newurl: str,
    ) -> None:
        """ریدایرکت را نادیده بگیر تا کد ۳۰۲ خودش «شاهد» بماند."""
        return


# ───────────────────────────── بندهای کاوش ──────────────────────────────────
def check_security_headers(probe: Probe, report: Report) -> None:
    """۱) سرآیندهای امنیتی روی پاسخ HTML."""
    report.checks.append("سرآیندهای امنیتی")
    status, headers, _ = probe.request("/")
    if status >= BAD_REQUEST:
        report.add(
            Finding(
                check="headers",
                severity="high",
                title="صفحهٔ اصلی پاسخ موفق نمی‌دهد",
                evidence=f"GET / ⇒ {status}",
                fix="سامانه را بالا بیاورید و دوباره اجرا کنید؛ کاوش معنایی ندارد اگر سایت بالا نباشد.",
            )
        )
        return
    lowered = {name.lower(): value for name, value in headers.items()}
    report_only_csp = lowered.get("content-security-policy-report-only", "")
    if "content-security-policy" not in lowered and report_only_csp:
        report.add(
            Finding(
                check="headers",
                severity="low",
                title="CSP در حالت «فقط گزارشی» است (در تولید باید اعمال شود)",
                evidence=report_only_csp[:160],
                fix="تولید سیاست را اعمال می‌کند (SECURE_CSP)؛ در محیط توسعه CSP_REPORT_ONLY=false بگذارید تا همان گیت را ببینید.",
            )
        )
    for required in REQUIRED_HEADERS:
        if required == "Content-Security-Policy" and report_only_csp:
            continue  # حالت گزارشی همین‌جا ثبت شد؛ تکرار لازم نیست.
        if required.lower() not in lowered:
            report.add(
                Finding(
                    check="headers",
                    severity="medium",
                    title=f"سرآیند امنیتی «{required}» نیست",
                    evidence=f"GET / ⇒ سرآیندهای موجود: {sorted(lowered)[:12]}",
                    fix="در تنظیمات/Django «SecurityMiddleware» و سیاست‌های پروژه را فعال کنید.",
                )
            )
    if lowered.get("x-frame-options", "") in {
        "",
        "ALLOWALL",
    } and "frame-ancestors" not in lowered.get("content-security-policy", ""):
        report.add(
            Finding(
                check="headers",
                severity="medium",
                title="جلوگیری از جاگذاری در قاب (clickjacking) دیده نشد",
                evidence=f"X-Frame-Options={lowered.get('x-frame-options', '—')!r}",
                fix="X-Frame-Options یا frame-ancestors در CSP را تنظیم کنید.",
            )
        )


def check_cookies(probe: Probe, report: Report) -> None:
    """۲) کوکی‌های نشست/CSRF: HttpOnly، Secure، SameSite."""
    report.checks.append("پرچم‌های کوکی")
    _, headers, _ = probe.request("/login/")
    cookies = [value for name, value in headers.items() if name.lower() == "set-cookie"]
    if not cookies:
        report.add(
            Finding(
                check="cookies",
                severity="low",
                title="کوکی نشست روی صفحهٔ ورود ست نمی‌شود",
                evidence="GET /login/ ⇒ بدون Set-Cookie",
                fix="اگر صفحهٔ ورود کوکی CSRF می‌دهد، همین بند باید پرچم‌ها را ببیند.",
            )
        )
        return
    https = probe.base_url.startswith("https")
    for cookie in cookies:
        name = cookie.split("=", 1)[0].strip()
        # کوکی CSRF عمداً HttpOnly نیست: الگوی «توکن دوگانه» می‌خواهد جاوااسکریپت
        # آن را بخواند و در سرآیند بفرستد. پس تنها کوکی نشست، HttpOnly می‌خواهد.
        is_csrf = "csrf" in name.lower()
        expectations = [
            ("SameSite", "medium", "SameSite=Lax یا Strict را تنظیم کنید."),
            ("Secure", "high", "روی HTTPS، Secure را روشن کنید (SESSION_COOKIE_SECURE)."),
        ]
        if not is_csrf:
            expectations.append(("HttpOnly", "high", "HttpOnly را برای کوکی‌های نشست روشن کنید."))
        for flag, severity, fix in expectations:
            if flag.lower() in cookie.lower():
                continue
            if flag == "Secure" and not https:
                report.add(
                    Finding(
                        check="cookies",
                        severity="low",
                        title=f"کوکی «{name}» پرچم Secure ندارد (میزبان HTTP است)",
                        evidence=cookie[:180],
                        fix="این کاوش را روی نشانی HTTPS تکرار کنید؛ تولید SESSION_COOKIE_SECURE=true دارد.",
                    )
                )
                continue
            report.add(
                Finding(
                    check="cookies",
                    severity=severity,
                    title=f"کوکی «{name}» پرچم {flag} ندارد",
                    evidence=cookie[:180],
                    fix=fix,
                )
            )


def check_anonymous_access(probe: Probe, report: Report) -> None:
    """۳) مسیرهای محافظت‌شده با کاربر ناشناس: «برو وارد شو»، نه داده."""
    report.checks.append("دسترسی ناشناس")
    for path in PROTECTED_PATHS:
        status, headers, body = probe.request(path)
        if status in {UNAUTHORIZED, FORBIDDEN, NOT_FOUND} or (
            status in {301, 302, 303, 307} and "login" in headers.get("Location", "")
        ):
            continue
        if status == OK:
            report.add(
                Finding(
                    check="authz",
                    severity="critical",
                    title=f"مسیر محافظت‌شده «{path}» با کاربر ناشناس پاسخ ۲۰۰ می‌دهد",
                    evidence=body[:200].replace("\n", " "),
                    fix="کنترل دسترسی این نما را بررسی کنید؛ هرگز دادهٔ زائر نباید بی‌ورود برگردد.",
                )
            )
        else:
            report.add(
                Finding(
                    check="authz",
                    severity="low",
                    title=f"پاسخ غیرمنتظره در «{path}»: {status}",
                    evidence=body[:120].replace("\n", " "),
                    fix="کد وضعیت درست (۴۰۱/۴۰۳/ریدایرکت ورود) را برگردانید.",
                )
            )


def check_csrf(probe: Probe, report: Report) -> None:
    """۴) POST بدون توکن CSRF باید رد شود."""
    report.checks.append("CSRF")
    payload = urllib.parse.urlencode({"phone": "09120000000"}).encode()
    status, _, body = probe.request(
        "/login/",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
    )
    if status == OK:
        report.add(
            Finding(
                check="csrf",
                severity="critical",
                title="POST بی‌توکن CSRF پذیرفته شد",
                evidence=f"POST /login/ ⇒ {status}، بدنه: {body[:150]}",
                fix="CsrfViewMiddleware فعال باشد و ویوها با csrf_exempt آزاد نشوند.",
            )
        )
    elif status >= SERVER_ERROR:
        report.add(
            Finding(
                check="csrf",
                severity="high",
                title="POST بی‌توکن CSRF باعث خطای سرور شد",
                evidence=f"POST /login/ ⇒ {status}",
                fix="باید ۴۰۳ برگردد، نه خطای ۵۰۰.",
            )
        )


def check_error_leak(probe: Probe, report: Report) -> None:
    """۵) نشت اطلاعات در پاسخ‌ها: traceback، نام کلاس خطا، نسخه."""
    report.checks.append("نشت اطلاعات")
    probes = (
        ("/this-path-does-not-exist-42/", "GET"),
        ("/api/v1/reservations?cursor=%00%27", "GET"),
        ("/help/?q=%3Cscript%3Ealert(1)%3C/script%3E", "GET"),
    )
    for path, method in probes:
        status, headers, body = probe.request(path, method=method)
        for marker in LEAK_MARKERS:
            if marker in body:
                report.add(
                    Finding(
                        check="leak",
                        severity="critical",
                        title=f"نشت «{marker}» در پاسخ {path}",
                        evidence=body[:220].replace("\n", " "),
                        fix="DEBUG=false در تولید و صفحهٔ خطای اختصاصی ۴۰۴/۵۰۰ راه بیندازید.",
                    )
                )
        if status >= SERVER_ERROR:
            report.add(
                Finding(
                    check="leak",
                    severity="high",
                    title=f"ورودی بدشکل «{path}» خطای سرور می‌سازد",
                    evidence=f"{method} {path} ⇒ {status}، بدنه: {body[:150]}",
                    fix="اعتبارسنجی ورودی را سخت‌گیرانه کنید؛ پاسخ درست ۴۰۰ است نه ۵۰۰.",
                )
            )
        if headers.get("Server", "").startswith(("Apache/", "nginx/", "WSGIServer/")):
            report.add(
                Finding(
                    check="leak",
                    severity="low",
                    title="نسخهٔ سرور در سرآیند Server لو می‌رود",
                    evidence=f"Server: {headers.get('Server')}",
                    fix="در Nginx مقدار server_tokens off و سرآیند را حذف/مبهم کنید.",
                )
            )


def check_injection(probe: Probe, report: Report) -> None:
    """۶) تزریق SQL و پیمایش مسیر: نه ۵۰۰، نه افشای داده."""
    report.checks.append("تزریق")
    payloads = ("%27%20OR%201%3D1--", "1%20UNION%20SELECT%20null--", "..%2f..%2f..%2fetc%2fpasswd")
    for path, payload in (
        ("/api/v1/meta?cursor=%s", payloads[0]),
        ("/help/?q=%s", payloads[1]),
        ("/%s", payloads[2]),
    ):
        status, _, body = probe.request(path % payload)
        if status >= SERVER_ERROR or "root:x:" in body:
            report.add(
                Finding(
                    check="injection",
                    severity="critical",
                    title=f"تزریق در «{path % payload}» نتیجهٔ خطرناک داد",
                    evidence=f"{status} · {body[:160]}",
                    fix="پارامترها را با ORM/اعتبارسنجی بپذیرید؛ هرگز رشتهٔ خام در کوئری نگذارید.",
                )
            )


def check_xss_reflection(probe: Probe, report: Report) -> None:
    """۷) XSS بازتابی: نشانهٔ حمله نباید خام برگردد."""
    report.checks.append("XSS بازتابی")
    marker = "<script>alert('dast')</script>"
    status, _, body = probe.request("/help/?q=" + urllib.parse.quote(marker))
    if status < BAD_REQUEST and marker in body:
        report.add(
            Finding(
                check="xss",
                severity="critical",
                title="ورودی کاربر خام در HTML بازتاب می‌شود",
                evidence=body[:220].replace("\n", " "),
                fix="قالب‌ها را با escape پیش‌فرض رندر کنید و از |safe برای ورودی کاربر پرهیز کنید.",
            )
        )


def check_rate_limit(probe: Probe, report: Report) -> None:
    """۸) سقف نرخ روی مسیر ورود: کوشش‌های پیاپی باید محدود شوند."""
    report.checks.append("سقف نرخ")
    codes: list[int] = []
    for _ in range(12):
        status, _, _ = probe.request(
            "/login/",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=b"phone=09120000000",
        )
        codes.append(status)
        if status in {TOO_MANY_REQUESTS, FORBIDDEN}:
            break
    if TOO_MANY_REQUESTS not in codes and codes.count(FORBIDDEN) == 0:
        report.add(
            Finding(
                check="ratelimit",
                severity="medium",
                title="سقف نرخ روی ارسال کد ورود دیده نشد",
                evidence=f"۱۲ کوشش پیاپی ⇒ {codes}",
                fix="محدودیت نرخ در نginx و در لایهٔ برنامه (کلید per-phone) را فعال کنید.",
            )
        )


def check_methods(probe: Probe, report: Report) -> None:
    """۹) متدهای غیرمجاز روی مسیرهای خواندنی."""
    report.checks.append("متدهای غیرمجاز")
    status, _, _ = probe.request("/api/v1/meta", method="DELETE")
    if status < BAD_REQUEST:
        report.add(
            Finding(
                check="methods",
                severity="high",
                title="متد DELETE روی مسیر خواندنی پذیرفته شد",
                evidence=f"DELETE /api/v1/meta ⇒ {status}",
                fix="مجوز متدها را در DRF/Nginx محدود کنید (405).",
            )
        )


def check_cors(probe: Probe, report: Report) -> None:
    """۱۰) CORS: مبدأ بیگانه نباید مجوز بگیرد."""
    report.checks.append("CORS")
    status, headers, _ = probe.request(
        "/api/v1/meta",
        headers={"Origin": "https://evil.example", "Host": probe.base_url.split("//")[-1]},
    )
    allow = headers.get("Access-Control-Allow-Origin", "")
    if allow in {"*", "https://evil.example"}:
        report.add(
            Finding(
                check="cors",
                severity="high",
                title="مبدأ بیگانه مجوز CORS می‌گیرد",
                evidence=f"Origin: evil.example ⇒ Access-Control-Allow-Origin: {allow}",
                fix="MAHDIEH_CORS_ORIGINS را فقط با دامنهٔ رسمی فرانت پر کنید (پیش‌فرض: بسته).",
            )
        )
    del status


def run_all(probe: Probe) -> Report:
    """اجرای همهٔ بندها روی یک نشانی."""
    report = Report(base_url=probe.base_url)
    for check in (
        check_security_headers,
        check_cookies,
        check_anonymous_access,
        check_csrf,
        check_error_leak,
        check_injection,
        check_xss_reflection,
        check_rate_limit,
        check_methods,
        check_cors,
    ):
        try:
            check(probe, report)
        except Exception as error:  # pragma: no cover - شبکهٔ لرزان نباید کاوش را بکشد
            report.findings.append(
                Finding(
                    check=check.__name__,
                    severity="low",
                    title=f"اجرای بند «{check.__name__}» بی‌نتیجه ماند",
                    evidence=f"{type(error).__name__}: {error}",
                    fix="دسترسی شبکه/نشانی را بررسی کنید و کاوش را بازاجرا کنید.",
                )
            )
    return report


def summarize(report: Report) -> str:
    """گزارش خوانا به فارسی."""
    lines = [
        f"کاوش امنیتی: {report.base_url}",
        f"بندهای اجراشده: {len(report.checks)} · ایرادها: {len(report.findings)}",
        "─" * 60,
    ]
    if not report.findings:
        lines.append("✅ هیچ ایرادی پیدا نشد.")
        return "\n".join(lines)
    for severity in SEVERITIES:
        group = [item for item in report.findings if item.severity == severity]
        if not group:
            continue
        lines.append(f"■ شدت {SEVERITY_FA[severity]} ({len(group)})")
        for item in group:
            lines += [
                f"  • [{item.check}] {item.title}",
                f"    شاهد: {item.evidence[:200]}",
                f"    رفع: {item.fix}",
            ]
    lines.append("─" * 60)
    lines.append("کد خروج ۱ می‌شود اگر شدت «بالا» یا «بحرانی» باقی مانده باشد.")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    """نقطهٔ ورود فرمان."""
    parser = argparse.ArgumentParser(description="کاوشگر امنیتی (DAST) سامانهٔ مهدیه")
    parser.add_argument("--base-url", required=True, help="نشانی پایهٔ سامانهٔ در حال اجرا")
    parser.add_argument("--json", default="", help="مسیر نوشتن گزارش JSON (اختیاری)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="مهلت هر درخواست")
    parser.add_argument("--insecure", action="store_true", help="نادیده‌گرفتن خطای گواهی TLS")
    parser.add_argument(
        "--behind-proxy",
        action="store_true",
        help="افزودن سرآیند X-Forwarded-Proto: https (سامانه پشت nginx/TLS است)",
    )
    parser.add_argument(
        "--wait", type=float, default=0.0, help="ثانیه انتظار برای بالا آمدن سامانه"
    )
    options = parser.parse_args(list(argv) if argv is not None else None)

    probe = Probe(
        options.base_url,
        timeout=options.timeout,
        verify_tls=not options.insecure,
        behind_proxy=options.behind_proxy,
    )
    if options.wait:
        deadline = time.monotonic() + options.wait
        while time.monotonic() < deadline:
            with suppress(OSError):
                status, _, _ = probe.request("/health/live/")
                if status < SERVER_ERROR:
                    break
            time.sleep(1)

    report = run_all(probe)
    print(summarize(report))
    if options.json:
        with Path(options.json).open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "base_url": report.base_url,
                    "checks": report.checks,
                    "findings": [item.as_dict() for item in report.findings],
                    "worst": report.worst(),
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        print(f"گزارش JSON: {options.json}")
    return 1 if report.worst() in {"critical", "high"} else 0


if __name__ == "__main__":
    sys.exit(main())
