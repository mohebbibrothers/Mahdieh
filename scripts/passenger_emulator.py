"""شبیه‌ساز کمینهٔ Passenger برای آزمون سرتاسری مجموعهٔ استقرار cPanel.

رفتارهایی از Passenger که ``deploy/cpanel/deploy.sh`` به آن‌ها تکیه دارد، و همین‌ها
را شبیه‌سازی می‌کند:

* اپ از ``<MAHDIEH_HOME>/app/passenger_wsgi.py`` بارگذاری می‌شود (ریشهٔ برنامه).
* لمس ``app/tmp/restart.txt`` ⇐ فرآیند کارگر کنار می‌رود و فرآیند تازه، ``current``
  را از نو حل می‌کند (دقیقاً همان سازوکار بازنشانی نرم Passenger).

فقط برای CI و آزمون محلی است؛ روی میزبان واقعی Passenger این نقش را دارد.

استفاده::

    MAHDIEH_HOME=~/mahdieh python scripts/passenger_emulator.py --port 8765
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, make_server


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def _serve(app_root: Path, port: int) -> None:
    os.chdir(app_root)
    spec = importlib.util.spec_from_file_location("passenger_wsgi", app_root / "passenger_wsgi.py")
    if spec is None or spec.loader is None:
        msg = "passenger_wsgi.py بارگذاری نشد"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    make_server("127.0.0.1", port, module.application, handler_class=_QuietHandler).serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="شبیه‌ساز Passenger مهدیه")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    home = Path(os.environ.get("MAHDIEH_HOME") or Path.home() / "mahdieh")
    app_root = home / "app"
    if args.child:
        _serve(app_root, args.port)
        return 0

    marker = app_root / "tmp" / "restart.txt"
    child: subprocess.Popen[bytes] | None = None
    stamp: float | None = None
    while True:
        mtime = marker.stat().st_mtime if marker.exists() else None
        ready = (app_root / "passenger_wsgi.py").exists()
        if ready and (child is None or mtime != stamp or child.poll() is not None):
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            child = subprocess.Popen(  # noqa: S603
                [sys.executable, __file__, "--child", "--port", str(args.port)]
            )
            stamp = mtime
            print(f"↻ کارگر تازه (pid {child.pid})", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
