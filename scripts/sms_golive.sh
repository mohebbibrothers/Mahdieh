#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# اسکریپت «روزِ راه‌اندازی پیامک» — کد الگوها که رسید، همین یک فرمان همه‌چیز.
#
# چرا این اسکریپت وجود دارد؟ آن روز، ریسکی‌ترین روز پروژه است: ۱۱ کد الگو باید
# بی‌غلط در فایل محیط تولید بنشیند، سرویس بی‌خطا بالا بیاید و پیام واقعی به موبایل
# برسد. اگر این کار با «دستی باز کن، دستی بنویس، دستی ری‌استارت کن» انجام شود،
# سه چیز محتمل است: یک کد جا بیفتد، یک کد اشتباه تایپ شود، و کسی نفهمد کدام.
#
# این اسکریپت آن سه ریسک را حذف می‌کند:
#   ۱. «صحت‌سنجی»: هر کد را با شکل رسمی پنل می‌سنجد و کدهای حیاتی را اجباری می‌داند.
#   ۲. «نوشتن اتمی»: فایل محیط با پشتیبان و دسترسی ۶۰۰ و بی‌پارازیت بازنویسی می‌شود
#      (سایر خطوط دست‌نخورده می‌مانند؛ رمزها همه سرجایشان).
#   ۳. «اثبات»: سرویس را بالا می‌آورد، سلامت را می‌سنجد و با sms_selfcheck **یک
#      پیام واقعی** می‌فرستد؛ اگر نرسید، با کد خروج ۱ تمام می‌شود.
#
# کاربردها:
#   scripts/sms_golive.sh --check                      # فقط بازرسی، بی‌تغییر
#   scripts/sms_golive.sh --env /etc/mahdieh/mahdieh.env \
#       --codes "offer_created=AbC123,otp_login=XyZ789"
#   scripts/sms_golive.sh --codes-file /root/codes.txt --to 09120000000 --restart
#   scripts/sms_golive.sh --rollback                   # بازگشت به پشتیبان
#   scripts/sms_golive.sh --codes "…" --no-verify      # فقط نوشتن، بدون بازرسی سرویس
#
# قالب فایل کدها (codes-file): هر خط «کلید=کد» یا «کلید: کد»؛ خطوط # نظر هستند.
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail

ENV_FILE="${MAHDIEH_ENV_FILE:-/etc/mahdieh/mahdieh.env}"
SERVICE="${MAHDIEH_SERVICE:-mahdieh}"
APP_DIR="${MAHDIEH_APP_DIR:-/srv/mahdieh}"
PYTHON_BIN="${MAHDIEH_PYTHON:-$APP_DIR/.venv/bin/python}"
MANAGE="${MAHDIEH_MANAGE:-$APP_DIR/src/manage.py}"
CODES=""
CODES_FILE=""
TEST_TO=""
MODE="apply"
DO_RESTART=0
RUN_SELFTEST=0
ALLOW_MISSING=0
NO_VERIFY=0
HEALTH_URL="${MAHDIEH_HEALTH_URL:-http://127.0.0.1:8000/health/ready/}"
HEALTH_TIMEOUT="${MAHDIEH_HEALTH_TIMEOUT:-60}"

# کدهای **حیاتی**: نبودشان یعنی زائر نمی‌تواند وارد شود یا رزروش تأیید نمی‌شود.
CRITICAL_KEYS=(otp_login booking_confirmed offer_created)
# کدهای **توصیه‌شده**: نبودشان کیفیت را کم می‌کند، نه دسترسی را.
EXPECTED_KEYS=(otp_login welcome booking_confirmed offer_created booking_reminder \
  booking_cancelled booking_moved offer_accepted waitlist_joined waitlist_requeued \
  feedback_request)

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --app-dir) APP_DIR="$2"; PYTHON_BIN="$APP_DIR/.venv/bin/python"; MANAGE="$APP_DIR/src/manage.py"; shift 2 ;;
    --codes) CODES="$2"; shift 2 ;;
    --codes-file) CODES_FILE="$2"; shift 2 ;;
    --to) TEST_TO="$2"; shift 2 ;;
    --health-url) HEALTH_URL="$2"; shift 2 ;;
    --check) MODE="check"; shift ;;
    --restart) DO_RESTART=1; shift ;;
    --selftest) RUN_SELFTEST=1; shift ;;
    --allow-missing) ALLOW_MISSING=1; shift ;;
    --no-verify) NO_VERIFY=1; shift ;;
    --rollback) MODE="rollback"; shift ;;
    -h|--help) usage ;;
    *) die "گزینهٔ ناشناخته: $1 (برای راهنما: --help)" ;;
  esac
done

[ -f "$ENV_FILE" ] || die "فایل محیط پیدا نشد: $ENV_FILE"

# ── جمع‌آوری کدها از آرگومان و فایل ─────────────────────────────────────────
PAIRS=""
if [ -n "$CODES_FILE" ]; then
  [ -f "$CODES_FILE" ] || die "فایل کدها پیدا نشد: $CODES_FILE"
  PAIRS="$(grep -vE '^\s*(#|$)' "$CODES_FILE" | tr ':' '=')"
fi
if [ -n "$CODES" ]; then
  PAIRS="$(printf '%s\n%s' "$PAIRS" "$(printf '%s' "$CODES" | tr ',' '\n')")"
fi

# ── قفل نوشتن ───────────────────────────────────────────────────────────────
# قفل به ازای «همین فایل محیط» است، نه سراسری: دو اجرا (اعمال یا بازگشت) روی یک
# فایل هم‌زمان نمی‌شوند، ولی اجرای مستقل روی فایل‌های جدا یکدیگر را سد نمی‌کنند.
take_lock() {
  LOCK_FILE="${ENV_FILE}.lock"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "اجرای دیگری از همین اسکریپت روی همین فایل در جریان است ($LOCK_FILE)"
}

# ── حالت بازگشت ─────────────────────────────────────────────────────────────
if [ "$MODE" = "rollback" ]; then
  take_lock
  BACKUP="$(ls -1t "${ENV_FILE}.bak."* 2>/dev/null | head -1 || true)"
  [ -n "$BACKUP" ] || die "پشتیبانی از فایل محیط پیدا نشد (${ENV_FILE}.bak.*)"
  say "بازگشت فایل محیط از: $BACKUP"
  cp --preserve=mode,ownership "$BACKUP" "$ENV_FILE"
  ok "فایل محیط بازگردانده شد"
  if [ "$DO_RESTART" -eq 1 ]; then
    systemctl restart "$SERVICE" && ok "سرویس «$SERVICE» ری‌استارت شد"
  fi
  exit 0
fi

# ── بازرسی کدها: شکل، تکراری‌بودن، کلید ناشناخته، کدهای حیاتی ───────────────
KEYS=() ; VALUES=()
declare -A SEEN=()
while IFS= read -r line; do
  [ -z "${line// }" ] && continue
  key="$(printf '%s' "${line%%=*}" | tr -d '[:space:]')"
  value="$(printf '%s' "${line#*=}" | tr -d '[:space:]')"
  [ -n "$key" ] || die "خط بی‌کلید: «$line»"
  [[ "$key" =~ ^[a-z_]+$ ]] || die "کلید نامعتبر: «$key» (فقط حروف کوچک و _)"
  [ -n "$value" ] || die "کد خالی برای «$key»"
  [[ "$value" =~ ^[A-Za-z0-9_-]{6,24}$ ]] || die "کد نامعتبر برای «$key»: «$value» (۶ تا ۲۴ نویسهٔ لاتین/عدد)"
  [ -n "${SEEN[$key]:-}" ] && die "کلید تکراری در ورودی: «$key»"
  SEEN[$key]=1
  KEYS+=("$key") ; VALUES+=("$value")
done <<<"$PAIRS"

KNOWN="otp_login welcome booking_confirmed booking_cancelled booking_moved booking_reminder \
offer_created offer_accepted waitlist_joined waitlist_requeued feedback_request staff_alert dry_run"
for key in "${KEYS[@]}"; do
  printf '%s\n' $KNOWN | grep -qx "$key" || warn "کلید ناشناخته: «$key» (نادیده گرفته نمی‌شود، ولی در کد مصرفی نیست)"
done

MISSING_CRITICAL=()
for key in "${CRITICAL_KEYS[@]}"; do
  [ -n "${SEEN[$key]:-}" ] || MISSING_CRITICAL+=("$key")
done
MISSING_EXPECTED=()
for key in "${EXPECTED_KEYS[@]}"; do
  [ -n "${SEEN[$key]:-}" ] || MISSING_EXPECTED+=("$key")
done

say "بازرسی ورودی: $(printf '%s' "${#KEYS[@]}") کد"
if [ "${#MISSING_CRITICAL[@]}" -gt 0 ]; then
  if [ "$ALLOW_MISSING" -eq 1 ]; then
    warn "کدهای حیاتی غایب (با --allow-missing پذیرفته شد): ${MISSING_CRITICAL[*]}"
  else
    die "کدهای حیاتی غایب: ${MISSING_CRITICAL[*]} — اگر عمدی است، --allow-missing بدهید."
  fi
fi
[ "${#MISSING_EXPECTED[@]}" -gt 0 ] && warn "کدهای توصیه‌شدهٔ غایب: ${MISSING_EXPECTED[*]}"
ok "شکل و یکتایی کدها درست است"

if [ "$MODE" = "check" ]; then
  ok "حالت بازرسی — هیچ تغییری اعمال نشد."
  exit 0
fi

# ── نوشتن اتمی در فایل محیط ─────────────────────────────────────────────────
take_lock

STAMP="$(date +%Y%m%d-%H%M%S-%N)"
BACKUP="${ENV_FILE}.bak.${STAMP}"
cp --preserve=mode,ownership "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP" 2>/dev/null || true
ok "پشتیبان گرفته شد: $BACKUP"

JSON="{"
first=1
for i in "${!KEYS[@]}"; do
  [ "$first" -eq 1 ] || JSON+=", "
  JSON+="\"${KEYS[$i]}\": \"${VALUES[$i]}\""
  first=0
done
JSON+="}"

TMP="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

# سایر خطوط دست‌نخورده؛ فقط خط SMS_PATTERN_CODES بازنویسی/جانشین می‌شود.
if grep -qE '^[[:space:]]*(export[[:space:]]+)?SMS_PATTERN_CODES=' "$ENV_FILE"; then
  python3 - "$ENV_FILE" "$TMP" "$JSON" <<'PY'
import re, sys
src, dst, value = sys.argv[1], sys.argv[2], sys.argv[3]
pattern = re.compile(r'^[ \t]*(export[ \t]+)?SMS_PATTERN_CODES=.*$', re.MULTILINE)
text = open(src, encoding='utf-8').read()
new, count = pattern.subn(f'SMS_PATTERN_CODES={value}', text)
if count != 1:
    raise SystemExit(f'انتظار یک خط SMS_PATTERN_CODES داشتم، {count} دیدم.')
open(dst, 'w', encoding='utf-8').write(new)
PY
else
  # سطر نبود ⇒ بی‌سر‌و‌صدا در یک بخش روشن افزوده می‌شود.
  cp "$ENV_FILE" "$TMP"
  {
    printf '\n# ── کد الگوهای پیامک (ثبت‌شده در پنل پس از بالا آمدن دامنه) ──\n'
    printf 'SMS_PATTERN_CODES=%s\n' "$JSON"
  } >> "$TMP"
fi

# اعتبارسنجی JSON پیش از جانشینی — «فایل محیط خراب» بدترین حالت است.
python3 -c "import json,sys; json.loads(sys.argv[1])" "$JSON" || die "JSON ساخته‌شده معتبر نیست"
install -m 600 --owner="$(stat -c %U "$ENV_FILE")" --group="$(stat -c %G "$ENV_FILE")" "$TMP" "$ENV_FILE"
ok "فایل محیط به‌روز شد ($(printf '%s' "${#KEYS[@]}") کد · دسترسی ۶۰۰)"

# ── بازآوردن سرویس و اثبات با پیام واقعی ────────────────────────────────────
if [ "$DO_RESTART" -eq 1 ]; then
  say "ری‌استارت سرویس «$SERVICE»"
  systemctl restart "$SERVICE"
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  until curl -fsS -o /dev/null "$HEALTH_URL"; do
    [ "$(date +%s)" -lt "$deadline" ] || die "سلامت سرویس در ${HEALTH_TIMEOUT} ثانیه برنگشت: $HEALTH_URL"
    sleep 2
  done
  ok "سرویس سالم بالا آمد"
else
  warn "بدون --restart، سرویس ری‌استارت نشد (کدها پس از ری‌استارت مؤثرند)"
fi

if [ "$NO_VERIFY" -eq 1 ]; then
  warn "بازرسی سرویس رد شد (--no-verify) — پیش از اعتماد، خودتان sms_patterns --check بزنید"
elif [ -x "$PYTHON_BIN" ]; then
  say "بازرسی کدهای فعال در سرویس"
  # همان کدهایی که تازه نوشتیم را صریح می‌دهیم: این هم «آنچه نوشتیم خوانده می‌شود»
  # را ثابت می‌کند و هم اگر JSON در فایل محیط خراب شده باشد، همین‌جا لو می‌رود.
  SMS_PATTERN_CODES="$JSON" "$PYTHON_BIN" "$MANAGE" sms_patterns --check \
    || die "پس از فعال‌سازی، قالب حیاتی هنوز بی‌کد است"
else
  warn "مفسر پروژه پیدا نشد ($PYTHON_BIN) — بازرسی سرویس رد شد؛ خودتان sms_patterns --check بزنید"
fi

if [ "$RUN_SELFTEST" -eq 1 ] || [ -n "$TEST_TO" ]; then
  [ -n "$TEST_TO" ] || die "برای --selftest شمارهٔ --to لازم است"
  say "آزمون واقعی ارسال به $TEST_TO"
  "$PYTHON_BIN" "$MANAGE" sms_selfcheck --to "$TEST_TO" --confirm || die "آزمون پیامک موفق نبود"
  ok "پیام آزمون ارسال شد؛ اگر به موبایل نرسید، گزارش پنل را نگاه کنید"
fi

cat <<EOF

  ═══════════════════════════════════════════════════════════════════════
   پیامک فعال شد.
   • پشتیبان فایل محیط: $BACKUP
   • بازگشت (در صورت بروز مشکل):  $0 --rollback --restart
   • گزارش وضعیت:                 python src/manage.py sms_patterns
   ═══════════════════════════════════════════════════════════════════════
EOF
