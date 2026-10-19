"""آزمون درون‌فرآیندیِ یک انتشار، **پیش از** آن‌که ترافیک واقعی به آن برسد.

همان پوستهٔ ``passenger_wsgi.py`` را بارگذاری می‌کند، اپ جنگو را از انتشار داده‌شده
می‌سازد و چند درخواست WSGI را مستقیم (بدون شبکه) به آن می‌دهد:

* ``/health/ready/``  ⇐ باید ۲۰۰ و ``status == healthy`` باشد (پایگاه‌داده، کش، مهاجرت)
* ``/health/live/``   ⇐ شناسهٔ انتشار باید همانی باشد که انتظار می‌رود
* ``/``               ⇐ صفحهٔ اصلی باید ۲۰۰ بدهد (قالب‌ها و دارایی‌های ایستا)

استفاده::

    python wsgi_probe.py RELEASE_DIR --host mahdei.n-cpanel.xyz [--expect-release ID]

کد خروج ۰ یعنی انتشار آمادهٔ سرویس است؛ هر چیز دیگر یعنی جابه‌جایی انجام نشود.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from typing import Any


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def _load_shim() -> Any:
    os.environ["MAHDIEH_WSGI_NO_AUTOLOAD"] = "1"
    path = Path(__file__).with_name("passenger_wsgi.py")
    spec = importlib.util.spec_from_file_location("mahdieh_passenger_shim", path)
    if spec is None or spec.loader is None:
        msg = f"پوستهٔ Passenger بارگذاری نشد: {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(app: Any, host: str, path: str) -> tuple[int, dict[str, str], bytes]:
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": host,
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": host,
        "HTTPS": "on",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_USER_AGENT": "mahdieh-wsgi-probe/1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
    }
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        del exc_info
        captured["status"] = int(status.split(" ", 1)[0])
        captured["headers"] = {k.lower(): v for k, v in headers}
        return lambda _data: None

    chunks = app(environ, start_response)
    try:
        body = b"".join(chunks)
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
    return captured["status"], captured["headers"], body


def main() -> int:
    parser = argparse.ArgumentParser(description="آزمون درون‌فرآیندی انتشار مهدیه")
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--expect-release", default="")
    args = parser.parse_args()

    shim = _load_shim()
    app = shim.load_application(args.release_dir)
    failures: list[str] = []

    status, _headers, body = _call(app, args.host, "/health/ready/")
    try:
        ready = json.loads(body)
    except ValueError:
        ready = {}
    if status != 200 or ready.get("status") != "healthy":  # noqa: PLR2004
        failures.append(f"آمادگی: کد {status} · {body[:400]!r}")
    else:
        _say(f"✓ آمادگی سالم ({', '.join(sorted(ready.get('checks', {})))})")

    status, _headers, body = _call(app, args.host, "/health/live/")
    live = json.loads(body) if status == 200 else {}  # noqa: PLR2004
    if args.expect_release and live.get("release") != args.expect_release:
        failures.append(f"شناسهٔ انتشار: «{live.get('release')}» ≠ «{args.expect_release}»")
    else:
        _say(f"✓ زنده؛ انتشار {live.get('release')}")

    status, headers, body = _call(app, args.host, "/")
    if status != 200:  # noqa: PLR2004
        failures.append(f"صفحهٔ اصلی: کد {status}")
    else:
        _say(f"✓ صفحهٔ اصلی ۲۰۰ ({len(body)} بایت · {headers.get('content-type', '?')})")

    for failure in failures:
        sys.stderr.write(f"✗ {failure}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
