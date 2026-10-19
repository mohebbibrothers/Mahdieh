#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# بازرسی پیش از انتشار روی سرور تولید — «آیا این سرور، آمادهٔ زائر است؟»
#
# چرا این اسکریپت لازم است؟ شبِ راه‌اندازی، هیچ‌کس وقت خواندن ۱۲ سند را ندارد.
# این اسکریپت همان ۱۲ سند را به ۱۸ بندِ اجراشدنی تبدیل می‌کند: هر بند یا ✓ است
# یا ✗ با یک جملهٔ روشن که «چه چیزی کم است و چرا مهم است».
#
# قاعده: این اسکریپت **هیچ‌چیز را تغییر نمی‌دهد** (فقط می‌خواند). اجرای دوباره‌اش
# بی‌خطر است و می‌توان آن را در چک‌لیست استقرار هم گذاشت.
#
# کاربرد:
#   sudo scripts/preflight_production.sh                 # بازرسی کامل
#   sudo scripts/preflight_production.sh --with-dast     # + کاوش امنیتی روی localhost
#   sudo scripts/preflight_production.sh --quiet         # فقط ایرادها
#
# کد خروج: شمار شمار ایرادهای «بلوکه‌کننده» (۰ = آمادهٔ انتشار).
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail

APP_DIR="${MAHDIEH_APP_DIR:-/srv/mahdieh}"
ENV_FILE="${MAHDIEH_ENV_FILE:-/etc/mahdieh/mahdieh.env}"
#: پروندهٔ وضعیت پایشگر — همان مسیری که واحد systemd اجازهٔ نوشتن دارد.
HEALTH_STATE="${MAHDIEH_HEALTH_STATE:-$APP_DIR/var/healthcheck.state}"
PYTHON_BIN="${MAHDIEH_PYTHON:-$APP_DIR/.venv/bin/python}"
MANAGE="${MAHDIEH_MANAGE:-$APP_DIR/src/manage.py}"
NGINX_CONF="${MAHDIEH_NGINX_CONF:-/etc/nginx/sites-enabled/mahdieh.conf}"
BACKUP_DIR="${MAHDIEH_BACKUP_DIR:-/var/backups/mahdieh}"
DRILL_LOG="${MAHDIEH_DRILL_LOG:-/var/log/mahdieh/restore-drill.log}"
HEALTH_URL="${MAHDIEH_HEALTH_URL:-http://127.0.0.1:8000/health/ready/}"
QUIET=0
WITH_DAST=0
FAILURES=0
WARNINGS=0

for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    --with-dast) WITH_DAST=1 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "گزینهٔ ناشناخته: $arg" >&2; exit 2 ;;
  esac
done

ok()   { [ "$QUIET" -eq 1 ] || printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf '  \033[1;33m!\033[0m %s\n' "$*"; }
fail() { FAILURES=$((FAILURES + 1)); printf '  \033[1;31m✗ %s\033[0m\n' "$*"; }
step() { [ "$QUIET" -eq 1 ] || printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ── ۱) پیکربندی محیط: هم متغیرها، هم مقادیر امنیتی ───────────────────────────
step "۱) متغیرهای محیط تولید"
if [ ! -f "$ENV_FILE" ]; then
  fail "فایل محیط نیست: $ENV_FILE (نمونه: deploy/env/mahdieh.env.example)"
else
  env_get() { grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' ; }
  require_value() {
    local name="$1" why="$2" value
    value="$(env_get "$name" || true)"
    if [ -z "${value// }" ]; then
      fail "$name خالی است — $why"
    else
      ok "$name تنظیم است"
    fi
  }
  require_value MAHDIEH_SECRET_KEY "کلید امضا؛ بدون آن نشست‌ها بی‌اعتبارند"
  require_value DATABASE_URL "اتصال پایگاه‌داده"
  require_value MAHDIEH_ALLOWED_HOSTS "دامنهٔ سایت؛ بدون آن جنگو همه را رد می‌کند"
  require_value FIELD_ENCRYPTION_KEY "رمزنگاری میدانی دادهٔ زائر"

  expect_value() {
    local name="$1" expected="$2" why="$3" value
    value="$(env_get "$name" || true)"
    if [ "$value" = "$expected" ]; then ok "$name=$expected"; else fail "$name باید $expected باشد ($why) — اکنون: «${value:-خالی}»"; fi
  }
  expect_value DEBUG "false" "حالت اشکال‌زدایی در تولید، نشت اطلاعات است"
  expect_value SESSION_COOKIE_SECURE "true" "کوکی نشست روی HTTPS"
  expect_value CSRF_COOKIE_SECURE "true" "کوکی CSRF روی HTTPS"
  expect_value SECURE_SSL_REDIRECT "true" "هدایت HTTP به HTTPS"

  phone="$(env_get MAHDIEH_SUPPORT_PHONE || true)"
  address="$(env_get MAHDIEH_SUPPORT_ADDRESS || true)"
  if [ -z "${phone// }" ]; then
    warn "MAHDIEH_SUPPORT_PHONE خالی است — زائر تا رسیدن شمارهٔ پذیرش، در پیامک راه تماس نمی‌بیند"
  else
    ok "شمارهٔ پذیرش تنظیم است"
  fi
  [ -n "${address// }" ] && ok "نشانی رسمی در محیط هست" || fail "MAHDIEH_SUPPORT_ADDRESS خالی است"
  # ── دو محل برای یک راز: اگر واگرا شوند، اپ و پشتیبان با دو گذرواژه کار می‌کنند ──
  # تنظیمات، DATABASE_PASSWORD را بر DATABASE_URL مقدم می‌داند؛ ولی اسکریپت‌های
  # پشتیبان/بازیابی ممکن است از دیگری بخوانند. واگرایی، خودش را فقط روز حادثه
  # نشان می‌دهد («پشتیبان گرفته شد، ولی بازگردانی احراز هویت نشد»). پس این‌جا،
  # پیش از حادثه، لو می‌رود.
  url_pass="$(env_get DATABASE_URL | sed -n 's|^[a-z]*://[^:/@]*:\([^@]*\)@.*|\1|p')"
  env_pass="$(env_get DATABASE_PASSWORD || true)"
  if [ -n "$env_pass" ] && [ -n "$url_pass" ] && [ "$env_pass" != "$url_pass" ]; then
    fail "DATABASE_PASSWORD با گذرواژهٔ داخل DATABASE_URL یکی نیست — اپ و پشتیبان با دو راز کار می‌کنند"
  elif [ -n "$env_pass" ] && [ -n "$url_pass" ]; then
    ok "گذرواژهٔ پایگاه‌داده یکسان است (متغیر و URL)"
  elif [ -n "$url_pass" ]; then
    ok "راز پایگاه‌داده یک نسخه دارد (داخل DATABASE_URL)"
  else
    fail "هیچ گذرواژهٔ پایگاه‌داده‌ای نیست — نه در URL نه در DATABASE_PASSWORD (اپ عمداً بالا نمی‌آید)"
  fi

  cors="$(env_get MAHDIEH_CORS_ORIGINS || true)"
  if [ -z "${cors// }" ]; then
    ok "MAHDIEH_CORS_ORIGINS بسته است (توصیهٔ پیش‌فرض تا آماده‌شدن فرانت)"
  else
    ok "MAHDIEH_CORS_ORIGINS تنظیم است: $cors"
  fi
fi

# ── ۲) بررسی سامانه و مهاجرت‌ها ──────────────────────────────────────────────
step "۲) بررسی جنگو و مهاجرت‌ها"
if [ -x "$PYTHON_BIN" ] && [ -f "$MANAGE" ]; then
  if "$PYTHON_BIN" "$MANAGE" check --deploy --fail-level WARNING >/tmp/preflight-check.log 2>&1; then
    ok "check --deploy بی‌ایراد"
  else
    fail "check --deploy ایراد دارد: $(tail -3 /tmp/preflight-check.log | tr '\n' ' ')"
    warn "اگر پیامک روشن است و کد الگو نرسیده، رانبوک docs/runbooks/sms-golive.md را ببینید"
  fi
  if "$PYTHON_BIN" "$MANAGE" makemigrations --check --dry-run >/dev/null 2>&1; then
    ok "مدل بدون مهاجرت (drift) نیست"
  else
    fail "drift مهاجرت: مدلی تغییر کرده و مهاجرتش ساخته نشده"
  fi
  plan="$("$PYTHON_BIN" "$MANAGE" migrate --plan 2>/dev/null | grep -c "apply all migrations" || true)"
  if [ "${plan:-0}" -eq 0 ]; then ok "صف مهاجرت خالی است (همه اعمال شده)"; else warn "مهاجرت اعمال‌نشده هست: $plan مورد"; fi
else
  fail "مفسر/مدیریت پیدا نشد: $PYTHON_BIN · $MANAGE"
fi

# ── ۳) سلامت زندهٔ سرویس ────────────────────────────────────────────────────
step "۳) سلامت سرویس"
if curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL"; then
  ok "بررسی آمادگی پاسخ می‌دهد: $HEALTH_URL"
else
  fail "بررسی آمادگی پاسخ نمی‌دهد — سرویس mahdieh بالا نیست: systemctl status mahdieh"
fi
if systemctl is-active --quiet mahdieh 2>/dev/null; then ok "سرویس mahdieh فعال است"; else warn "سرویس mahdieh فعال نیست (systemd در دسترس نیست یا خاموش است)"; fi

# ── ۴) وب‌سرور و سدهای نرخ ──────────────────────────────────────────────────
step "۴) Nginx و سدهای نرخ"
if [ -f "$NGINX_CONF" ]; then
  ok "پیکربندی Nginx موجود است: $NGINX_CONF"
  grep -q "limit_req_zone" "$NGINX_CONF" && ok "سد نرخ درخواست (limit_req_zone) فعال است" || fail "limit_req_zone در Nginx نیست (مسیر ورود باید محدود شود)"
  grep -q "limit_conn_zone" "$NGINX_CONF" && ok "سد اتصال هم‌زمان (limit_conn_zone) فعال است" || warn "limit_conn_zone نیست"
  grep -q "server_tokens off" "$NGINX_CONF" && ok "نسخهٔ سرور پنهان است" || warn "server_tokens off نیست (نسخهٔ Nginx لو می‌رود)"
  grep -q "127.0.0.1:8000" "$NGINX_CONF" && ok "به‌روی گونیکورن محلی اشاره می‌کند" || warn "upstream با 127.0.0.1:8000 نمی‌خواند — بررسی کنید"
else
  warn "پیکربندی Nginx پیدا نشد: $NGINX_CONF (اگر از مسیر دیگری سرو می‌کنید، با MAHDIEH_NGINX_CONF بدهید)"
fi
if have nginx; then
  if nginx -t >/dev/null 2>&1; then ok "پیکربندی Nginx معتبر است"; else fail "nginx -t خطا دارد"; fi
else
  warn "دستور nginx در این میزبان نیست"
fi

# ── ۵) سد ظرفیت بار (load shedding) ─────────────────────────────────────────
step "۵) سد ظرفیت و کارگرها"
if grep -q "ConcurrencyLimitMiddleware" "$APP_DIR/src/config/asgi.py" 2>/dev/null; then
  ok "سد ظرفیت در مسیر ASGI فعال است"
else
  warn "سد ظرفیت ASGI پیدا نشد (اگر روی WSGI سرو می‌کنید، سد اول Nginx است — ADR-0015)"
fi
if systemctl cat mahdieh 2>/dev/null | grep -q "\-k sync"; then
  ok "کارگر WSGI هم‌زمان (تصمیم ADR-0015)"
else
  warn "نوع کارگر سرویس با ADR-0015 نمی‌خواند — با docs/adr/0015 نگاه کنید"
fi

# ── ۶) پشتیبان و تمرین بازگردانی ───────────────────────────────────────────
step "۶) پشتیبان‌گیری و تمرین بازگردانی"
newest_backup="$(ls -1t "$BACKUP_DIR"/*.dump "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1 || true)"
if [ -n "$newest_backup" ]; then
  age_h=$(( ( $(date +%s) - $(stat -c %Y "$newest_backup") ) / 3600 ))
  if [ "$age_h" -le 24 ]; then ok "تازه‌ترین پشتیبان ${age_h} ساعت پیش"; else fail "تازه‌ترین پشتیبان ${age_h} ساعت قدمت دارد (باید < ۲۴ ساعت باشد)"; fi
  size="$(stat -c %s "$newest_backup")"
  if [ "$size" -gt 1024 ]; then ok "اندازهٔ پشتیبان معقول است ($((size / 1024)) کیلوبایت)"; else fail "پشتیبان کوچک‌تر از انتظار است — خالی به نظر می‌رسد"; fi
else
  fail "هیچ پشتیبانی در $BACKUP_DIR نیست"
fi
if [ -f "$DRILL_LOG" ]; then
  drill_days=$(( ( $(date +%s) - $(stat -c %Y "$DRILL_LOG") ) / 86400 ))
  if [ "$drill_days" -le 30 ]; then ok "تمرین بازگردانی ${drill_days} روز پیش انجام شده"; else warn "تمرین بازگردانی ${drill_days} روز قدمت دارد"; fi
else
  warn "گزارش تمرین بازگردانی نیست: $DRILL_LOG"
fi
for unit in mahdieh-backup.timer mahdieh-drill.timer mahdieh-reminders.timer mahdieh-retention.timer mahdieh-healthcheck.timer; do
  if systemctl is-enabled --quiet "$unit" 2>/dev/null; then ok "زمان‌بند $unit فعال است"; else warn "زمان‌بند $unit فعال نیست"; fi
done

# ── ۷) TLS ──────────────────────────────────────────────────────────────────
step "۷) گواهی TLS"
if have certbot; then
  days="$(certbot certificates 2>/dev/null | grep -oE 'VALID: [0-9]+ day' | grep -oE '[0-9]+' | sort -n | head -1 || true)"
  if [ -n "${days:-}" ] && [ "$days" -ge 14 ]; then ok "گواهی $days روز اعتبار دارد"; else warn "اعتبار گواهی کمتر از ۱۴ روز یا خوانده نشد — تمدید خودکار را بررسی کنید"; fi
else
  warn "certbot در این میزبان نیست (اگر TLS را جای دیگری تمدید می‌کنید، بی‌خطر است)"
fi

# ── ۸) پیامک: آمادگی یا ترتیب درست ──────────────────────────────────────────
step "۸) پیامک"
if [ -x "$PYTHON_BIN" ] && [ -f "$MANAGE" ]; then
  if "$PYTHON_BIN" "$MANAGE" sms_patterns --check >/tmp/preflight-sms.log 2>&1; then
    ok "همهٔ الگوهای حیاتی کد دارند (پیامک زنده است)"
  else
    warn "پیامک هنوز زنده نیست: $(grep -m1 'حیاتی' /tmp/preflight-sms.log || echo 'کد الگو نرسیده')"
    warn "ترتیب درست: نخست سایت با SMS_ENABLED=false بالا بیاید، سپس scripts/sms_golive.sh"
  fi
fi

# ── ۹) کاوش امنیتی روی همان سرور (اختیاری) ─────────────────────────────────
if [ "$WITH_DAST" -eq 1 ]; then
  step "۹) کاوش امنیتی روی میزبان (--with-dast)"
  if [ -x "$PYTHON_BIN" ] && [ -f "$APP_DIR/scripts/dast_probe.py" ]; then
    if "$PYTHON_BIN" "$APP_DIR/scripts/dast_probe.py" --base-url "http://127.0.0.1:8000" >/tmp/preflight-dast.log 2>&1; then
      ok "کاوش امنیتی: هیچ ایراد بالا/بحرانی نماند"
    else
      fail "کاوش امنیتی ایراد بالا/بحرانی دید: /tmp/preflight-dast.log"
    fi
  else
    warn "scripts/dast_probe.py روی سرور نیست"
  fi
else
  step "۹) کاوش امنیتی (با --with-dast اجرا می‌شود)"
fi

# ── ۱۰) منابع میزبان ───────────────────────────────────────────────────────
step "۱۰) منابع میزبان"
free_pct="$(df -Pk / | awk 'NR==2 {print int($4 / ($2 / 100))}')"
if [ "${free_pct:-0}" -ge 10 ]; then ok "فضای دیسک آزاد: ${free_pct}٪"; else fail "فضای دیسک آزاد ${free_pct}٪ — زیر ۱۰٪ خطرناک است"; fi
if have free; then
  mem_pct="$(free | awk 'NR==2 {print int($7 / ($2 / 100))}')"
  if [ "${mem_pct:-0}" -ge 10 ]; then ok "حافظهٔ آزاد: ${mem_pct}٪"; else warn "حافظهٔ آزاد ${mem_pct}٪"; fi
fi
# تازگیِ وضعیت پایش: پایشگری که اجرا نمی‌شود، بدتر از نبودنش است چون اطمینان دروغ می‌دهد.
if [ -f "$HEALTH_STATE" ]; then
  checked="$(python3 - "$HEALTH_STATE" <<'HEALTHPY' 2>/dev/null || true
import json, sys
from datetime import UTC, datetime
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    stamp = datetime.fromisoformat(data["checked_at"])
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    print(int((datetime.now(UTC) - stamp).total_seconds() // 60))
except Exception:
    print("")
HEALTHPY
)"
  if [ -n "$checked" ] && [ "$checked" -le 15 ]; then
    ok "پایش سلامت زنده است (آخرین اجرا ${checked} دقیقه پیش)"
  elif [ -n "$checked" ]; then
    warn "پایش سلامت از ${checked} دقیقه پیش اجرا نشده — systemctl status mahdieh-healthcheck.timer"
  else
    warn "پروندهٔ وضعیت پایش خوانده نشد: $HEALTH_STATE"
  fi
else
  warn "پروندهٔ وضعیت پایش نیست ($HEALTH_STATE) — هنوز هیچ پایشی اجرا نشده است"
fi

if systemctl list-jobs 2>/dev/null | grep -q "running"; then warn "کار systemd در جریان است — نتیجه ممکن است ناپایدار باشد"; fi

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  printf '\033[1;32m✅ بازرسی پیش از انتشار: بی‌ایراد\033[0m'
else
  printf '\033[1;31m✗ بازرسی پیش از انتشار: %s ایراد بلوکه‌کننده\033[0m' "$FAILURES"
fi
[ "$WARNINGS" -gt 0 ] && printf ' · %s هشدار' "$WARNINGS"
printf '\n'
exit "$FAILURES"
