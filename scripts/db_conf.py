#!/usr/bin/env python3
"""پیکربندی اتصال MariaDB برای ابزارهای خط فرمان (پشتیبان، بازیابی، تمرین).

راز پایگاه‌داده **یک جا** زندگی می‌کند: ``DATABASE_URL`` (و در صورت نیاز
``DATABASE_PASSWORD`` که اولویت دارد — همان قاعدهٔ ``config.settings.production``).
این ابزار آن را به یک «فایل پیش‌فرض» موقت با دسترسی ۶۰۰ برای
``mariadb``/``mariadb-dump`` تبدیل می‌کند تا گذرواژه هرگز در خط فرمان (و در
فهرست فرآیندها) دیده نشود.

استفاده:
    db_conf.py --write-defaults /tmp/x.cnf     # فایل [client] می‌سازد، نام پایگاه‌داده را چاپ می‌کند
    db_conf.py --name                          # فقط نام پایگاه‌داده
    db_conf.py --env-file ~/.mahdieh/mahdieh.env --name
    db_conf.py --url-for-db mahdieh_drill      # همان نشانی، پایگاه‌دادهٔ دیگر (برای manage.py)

فقط کتابخانهٔ استاندارد؛ پیش از نصب وابستگی‌ها هم کار می‌کند.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit, urlunsplit

SUPPORTED_SCHEMES = {"mysql", "mariadb", "mysql2"}
DEFAULT_PORT = 3306
#: کوتاه‌ترین مقدار نقل‌قول‌دار ممکن (دو علامت نقل‌قول).
_QUOTED_MIN_LEN = 2


def read_env_file(path: Path) -> dict[str, str]:
    """خواندن فایل محیط (``NAME=value``؛ خط خالی و توضیح نادیده)."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= _QUOTED_MIN_LEN and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def connection_settings(environ: dict[str, str]) -> dict[str, str]:
    """``DATABASE_URL`` ⇒ کلیدهای اتصال؛ خطای روشن در نبود یا قالب نادرست."""
    url = environ.get("DATABASE_URL", "").strip()
    if not url:
        msg = "DATABASE_URL تنظیم نشده است."
        raise SystemExit(msg)
    parts = urlsplit(url)
    if parts.scheme not in SUPPORTED_SCHEMES:
        msg = f"طرح نشانی پشتیبانی نمی‌شود: {parts.scheme!r} (باید mysql:// باشد)."
        raise SystemExit(msg)
    name = parts.path.lstrip("/")
    if not name:
        msg = "نام پایگاه‌داده در DATABASE_URL نیامده است."
        raise SystemExit(msg)
    password = environ.get("DATABASE_PASSWORD", "").strip() or unquote(parts.password or "")
    return {
        "user": unquote(parts.username or ""),
        "password": password,
        "host": parts.hostname or "localhost",
        "port": str(parts.port or DEFAULT_PORT),
        "database": unquote(name),
    }


def url_for_database(environ: dict[str, str], name: str) -> str:
    """همان ``DATABASE_URL`` با نام پایگاه‌دادهٔ دیگر (مثلاً قرنطینهٔ تمرین)."""
    parts = urlsplit(environ["DATABASE_URL"].strip())
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def defaults_file_text(settings: dict[str, str]) -> str:
    """متن فایل پیش‌فرض ``[client]`` (مقادیر نقل‌قول‌شده؛ بی‌نام پایگاه‌داده)."""

    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["[client]", f"user={quote(settings['user'])}"]
    if settings["password"]:
        lines.append(f"password={quote(settings['password'])}")
    lines += [
        f"host={quote(settings['host'])}",
        f"port={settings['port']}",
        "default-character-set=utf8mb4",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """نقطهٔ ورود خط فرمان."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, help="فایل محیط (اگر متغیرها در محیط نیستند)")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write-defaults", type=Path, metavar="PATH")
    action.add_argument("--name", action="store_true", help="فقط نام پایگاه‌داده")
    action.add_argument("--url-for-db", metavar="NAME", help="نشانی اتصال به پایگاه‌دادهٔ دیگر")
    options = parser.parse_args(argv)

    environ = dict(os.environ)
    if options.env_file:
        for key, value in read_env_file(options.env_file.expanduser()).items():
            environ.setdefault(key, value)
    settings = connection_settings(environ)

    if options.url_for_db:
        sys.stdout.write(url_for_database(environ, options.url_for_db) + "\n")
        return 0
    if options.write_defaults:
        target: Path = options.write_defaults
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(defaults_file_text(settings))
        Path(target).chmod(0o600)
    sys.stdout.write(settings["database"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
