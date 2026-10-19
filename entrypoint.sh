#!/usr/bin/env bash
# نقطهٔ ورود امن کانتینر — بدون عملیات مخرب، fail-fast در کانفیگ ناقص.
set -Eeuo pipefail

# ── گارد متغیرهای حیاتی: نبودشان «تصویر بالا می‌آید ولی سایت خراب است» می‌سازد ──
: "${MAHDIEH_SECRET_KEY:?MAHDIEH_SECRET_KEY تنظیم نشده است (راز نشست/امضا)}"
: "${DATABASE_URL:?DATABASE_URL تنظیم نشده است}"
: "${MAHDIEH_ALLOWED_HOSTS:?MAHDIEH_ALLOWED_HOSTS تنظیم نشده است (دامنهٔ سایت)}"

echo "▶ بررسی آمادگی دیتابیس..."
python - <<'PY'
import os
import sys
import time

sys.path.insert(0, "src")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
import django

django.setup()
from django.db import connection

for attempt in range(30):
    try:
        connection.ensure_connection()
        print("  ✅ دیتابیس آماده است")
        sys.exit(0)
    except Exception as exc:
        print(f"  … تلاش {attempt + 1}/30: {type(exc).__name__}")
        connection.close()
        time.sleep(2)
print("  ❌ دیتابیس در دسترس نیست", file=sys.stderr)
sys.exit(1)
PY

echo "▶ اجرای مهاجرت‌ها..."
python src/manage.py migrate --noinput

echo "▶ جدول کش (بی‌اثر اگر از قبل باشد)..."
python src/manage.py createcachetable

echo "▶ جمع‌آوری دارایی‌های ایستا..."
python src/manage.py collectstatic --noinput --clear

echo "▶ بررسی سیستم (حالت تولید، سخت‌گیرانه)..."
# یک دروازه، دو سبد: خطاهای واقعی انتشار را می‌بندند؛ هشدارهای **عمدی** پروژه
# (نشانی/تلفن پذیرش که پس از بالا آمدن دامنه می‌رسد، و پیامک که پنل پس از دامنه
# تأیید می‌کند) چاپ می‌شوند ولی سایت را قفل نمی‌کنند. دلیل کامل: کاربرگ
# «production_check» و docs/runbooks/sms-golive.md
python src/manage.py production_check || {
  cat >&2 <<'HINT'
  ─────────────────────────────────────────────────────────────────────
  استقرار متوقف شد: پیکربندی تولید ایراد **بلوکه‌کننده** دارد.
  دو علت رایج، به ترتیب احتمال:
    ۱) متغیری از محیط تولید خالی یا نادرست است
       (کلید امنیتی، دامنه، DEBUG، کوکی امن). فهرست کامل:
       deploy/env/mahdieh.env.example
    ۲) پیامک روشن است ولی کد الگوها هنوز از پنل نیامده‌اند.
       ترتیب درست: نخست سایت با SMS_ENABLED=false بالا بیاید، سپس:
         scripts/sms_golive.sh --codes-file ... --restart --selftest
  ─────────────────────────────────────────────────────────────────────
HINT
  exit 1
}

exec "$@"
