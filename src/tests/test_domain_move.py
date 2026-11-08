"""انتقال دامنه (mahdei.n-cpanel.xyz ⇒ rv8.ir) — دامنهٔ یکتا و پایش از درون میزبان.

دو یافتهٔ واقعی میزبان، هر کدام یک قرارداد:

1. **دامنهٔ یکتا:** هر نام مجازِ غیر از دامنهٔ ``SITE_URL`` (www، دامنهٔ قبلی) با ۳۰۱/۳۰۸ به
   همان دامنه می‌رود؛ نام نامجاز هنوز ۴۰۰ است؛ مسیر سلامت، IP و نام محلی هدایت نمی‌شوند.
2. **پین IP:** DNS درونی میزبان rv8.ir را به ‎10.10.34.35‎ (بسته) می‌برد. پایشگر سلامت،
   فرمان ``mahdieh`` و اسکریپت‌های استقرار باید با ``MAHDIEH_VERIFY_RESOLVE`` به IP عمومی
   وصل شوند — فقط برای همان دامنه، با همان نام میزبان (SNI/گواهی دست‌نخورده).
"""

from __future__ import annotations

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from types import ModuleType
from typing import Any

from django.core.management import call_command
from django.test import Client
import pytest

from apps.core.management.commands.healthcheck import EXIT_HEALTHY, EXIT_UNHEALTHY
from apps.core.netpin import normalize_pin, pinned_host

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED = "mahdieh-pin.invalid"  # ‎.invalid‎ هرگز در DNS واقعی حل نمی‌شود (RFC 6761)


# ── ۱) دامنهٔ یکتا ──────────────────────────────────────────────────────────────


@pytest.fixture
def canonical(settings: Any) -> Any:
    settings.SITE_URL = "https://rv8.ir"
    settings.ALLOWED_HOSTS = [
        "rv8.ir",
        "www.rv8.ir",
        "mahdei.n-cpanel.xyz",
        "testserver",
        "127.0.0.1",
    ]
    settings.CANONICAL_HOST_REDIRECT = True
    return settings


@pytest.mark.unit
@pytest.mark.parametrize("alias", ["www.rv8.ir", "WWW.RV8.IR", "mahdei.n-cpanel.xyz"])
def test_aliases_redirect_permanently_to_the_canonical_domain(canonical: Any, alias: str) -> None:
    response = Client().get("/units/A1/?day=2026-11-08&guests=4", HTTP_HOST=alias)
    assert response.status_code == 301
    assert response["Location"] == "https://rv8.ir/units/A1/?day=2026-11-08&guests=4"


@pytest.mark.unit
def test_non_safe_methods_keep_method_and_body_with_308(canonical: Any) -> None:
    response = Client().post("/api/v1/auth/otp/request", {"phone": "x"}, HTTP_HOST="www.rv8.ir")
    assert response.status_code == 308
    assert response["Location"] == "https://rv8.ir/api/v1/auth/otp/request"


@pytest.mark.unit
@pytest.mark.parametrize("host", ["testserver", "127.0.0.1"])
def test_local_names_and_ips_are_never_redirected(canonical: Any, host: str) -> None:
    assert Client().get("/robots.txt", HTTP_HOST=host).status_code != 301


@pytest.mark.unit
def test_health_probes_are_exempt(canonical: Any) -> None:
    response = Client().get("/health/live/", HTTP_HOST="www.rv8.ir")
    assert response.status_code == 200


@pytest.mark.unit
def test_disallowed_hosts_still_get_400(canonical: Any) -> None:
    assert Client().get("/", HTTP_HOST="evil.example").status_code == 400


@pytest.mark.unit
def test_redirect_is_off_unless_enabled(canonical: Any) -> None:
    canonical.CANONICAL_HOST_REDIRECT = False
    assert Client().get("/robots.txt", HTTP_HOST="www.rv8.ir").status_code == 200


@pytest.mark.unit
def test_production_enables_the_redirect_and_orders_it_before_security() -> None:
    production = (REPO_ROOT / "src" / "config" / "settings" / "production.py").read_text(
        encoding="utf-8"
    )
    assert (
        'CANONICAL_HOST_REDIRECT = env.bool("MAHDIEH_CANONICAL_REDIRECT", default=True)'
        in production
    )
    from django.conf import settings

    chain = list(settings.MIDDLEWARE)
    assert chain.index("apps.core.middleware.CanonicalHostMiddleware") < chain.index(
        "django.middleware.security.SecurityMiddleware"
    )


# ── ۲) پین IP ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pin_applies_only_to_the_named_host_and_is_restored() -> None:
    original = socket.getaddrinfo
    with pinned_host(PINNED, "127.0.0.1"):
        assert socket.getaddrinfo(PINNED, 80)[0][4][0] == "127.0.0.1"
        assert socket.getaddrinfo(PINNED.upper() + ".", 80)[0][4][0] == "127.0.0.1"
        assert socket.getaddrinfo("localhost", 80)  # نام‌های دیگر دست‌نخورده
    assert socket.getaddrinfo is original


@pytest.mark.unit
def test_pin_values_are_validated_not_guessed() -> None:
    assert normalize_pin("") == ""
    assert normalize_pin(" 85.198.13.88 ") == "85.198.13.88"
    assert normalize_pin("[2001:db8::1]") == "2001:db8::1"
    with pytest.raises(ValueError, match="does not appear"):
        normalize_pin("85.198.13.88; rm -rf")


@pytest.fixture
def ready_server() -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"status": "healthy", "checks": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _healthcheck(*args: str) -> int:
    with pytest.raises(SystemExit) as exit_info:
        call_command("healthcheck", *args)
    return int(exit_info.value.code or 0)


@pytest.mark.django_db
def test_healthcheck_reaches_its_own_domain_through_the_pin(
    ready_server: int, settings: Any, tmp_path: Path
) -> None:
    base = f"http://{PINNED}:{ready_server}"
    settings.SITE_URL = base
    settings.VERIFY_RESOLVE = ""
    args = (f"--base-url={base}", f"--state-file={tmp_path / 's'}", "--json")
    assert _healthcheck(*args) == EXIT_UNHEALTHY  # بی پین: نام حل نمی‌شود (= IP داخلیِ بسته)
    settings.VERIFY_RESOLVE = "127.0.0.1"
    assert _healthcheck(*args) == EXIT_HEALTHY


@pytest.mark.django_db
def test_healthcheck_never_pins_a_foreign_host(
    ready_server: int, settings: Any, tmp_path: Path
) -> None:
    settings.SITE_URL = "https://rv8.ir"
    settings.VERIFY_RESOLVE = "127.0.0.1"
    code = _healthcheck(
        f"--base-url=http://{PINNED}:{ready_server}", f"--state-file={tmp_path / 's'}", "--json"
    )
    assert code == EXIT_UNHEALTHY


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mahdieh_cli_pin", REPO_ROOT / "deploy" / "cpanel" / "mahdieh_cli.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass‌ها ماژول را از sys.modules می‌خوانند
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_cli_pins_only_the_site_domain_and_rejects_bad_ips() -> None:
    cli = _load_cli()
    try:
        assert cli.pin_resolution(PINNED, "not-an-ip") == ""
        assert cli.pin_resolution(PINNED, "[127.0.0.1]") == "127.0.0.1"
        assert socket.getaddrinfo(PINNED, 443)[0][4][0] == "127.0.0.1"
        assert socket.getaddrinfo("localhost", 443)
    finally:
        socket.getaddrinfo = cli._SYSTEM_GETADDRINFO
        sys.modules.pop("mahdieh_cli_pin", None)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(PINNED, 443)


@pytest.mark.unit
@pytest.mark.parametrize("script", ["deploy.sh", "front_deploy.sh"])
def test_deploy_scripts_read_and_validate_the_pin_from_the_env_file(
    script: str, tmp_path: Path
) -> None:
    """هر دو اسکریپت پین را از فایل محیط می‌خوانند و مقدار ناامن را نمی‌پذیرند."""
    source = (REPO_ROOT / "deploy" / "cpanel" / script).read_text(encoding="utf-8")
    start = source.index("env_file_value() {")
    end = source.index("\n}\n", source.index("load_resolve_ip() {")) + 3
    env_file = tmp_path / "mahdieh.env"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        'die() { printf "DIE %s\\n" "$*"; exit 3; }\n'
        f"{source[start:end]}\n"
        'load_resolve_ip; printf "SITE=%s PIN=%s\\n" "$(site_url)" "${RESOLVE_IP}"\n',
        encoding="utf-8",
    )
    bash = shutil.which("bash") or "/bin/bash"

    def run(content: str, pin: str = "") -> subprocess.CompletedProcess[str]:
        env_file.write_text(content, encoding="utf-8")
        env = {
            "PATH": os.environ["PATH"],
            "BASE_PY": sys.executable,
            "ENV_SOURCE": str(env_file),
            "RESOLVE_IP": pin,
        }
        return subprocess.run(  # noqa: S603 — بخش‌های همین اسکریپت مخزن
            [bash, str(harness)], env=env, capture_output=True, text=True, timeout=30, check=False
        )

    ok = run("MAHDIEH_SITE_URL=https://rv8.ir/\nMAHDIEH_VERIFY_RESOLVE=85.198.13.88\nNOTE=مهدیه\n")
    assert ok.stdout.strip() == "SITE=https://rv8.ir PIN=85.198.13.88", ok.stdout + ok.stderr
    v6 = run("MAHDIEH_SITE_URL=https://rv8.ir\n", pin="2001:db8::1")
    assert v6.stdout.strip().endswith("PIN=[2001:db8::1]")
    none = run("MAHDIEH_SITE_URL=https://rv8.ir\n")
    assert none.stdout.strip() == "SITE=https://rv8.ir PIN="
    bad = run("MAHDIEH_SITE_URL=https://rv8.ir\nMAHDIEH_VERIFY_RESOLVE=1.2.3.4;id\n")
    assert bad.returncode == 3
    assert "باید یک IP باشد" in bad.stdout
