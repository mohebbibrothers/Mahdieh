#!/usr/bin/env bash
# نقطهٔ ورود امن کانتینر — بدون عملیات مخرب، fail-fast در کانفیگ ناقص.
set -Eeuo pipefail

echo "▶ بررسی آمادگی دیتابیس..."
python - <<'PY'
import os, sys, time
import psycopg

dsn = os.environ["DATABASE_URL"]
for attempt in range(30):
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            print("  ✅ دیتابیس آماده است")
            sys.exit(0)
    except Exception as exc:
        print(f"  … تلاش {attempt + 1}/30: {type(exc).__name__}")
        time.sleep(2)
print("  ❌ دیتابیس در دسترس نیست", file=sys.stderr)
sys.exit(1)
PY

echo "▶ اجرای مهاجرت‌ها..."
python src/manage.py migrate --noinput

echo "▶ جمع‌آوری دارایی‌های ایستا..."
python src/manage.py collectstatic --noinput --clear

echo "▶ بررسی سیستم..."
python src/manage.py check --deploy --fail-level WARNING

exec "$@"
