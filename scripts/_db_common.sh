# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────────────
# پایهٔ مشترک ابزارهای پایگاه‌داده (پشتیبان، بازیابی، تمرین) — فقط source شود.
#
# • مفسر پایتون: MAHDIEH_PYTHON ⇒ .venv مخزن ⇒ python3 (روی میزبان cPanel
#   python3 در PATH نیست؛ کرون MAHDIEH_PYTHON را به venv برنامه می‌دهد).
# • فایل محیط: MAHDIEH_ENV_FILE ⇒ src/.env (همان فایلی که جنگو می‌خواند).
# • راز پایگاه‌داده در یک «فایل پیش‌فرض» موقت ۶۰۰ نوشته می‌شود و با خروج
#   اسکریپت پاک می‌شود؛ هرگز در خط فرمان (و فهرست فرآیندها) دیده نمی‌شود.
# ─────────────────────────────────────────────────────────────────────────────

: "${REPO_ROOT:?REPO_ROOT پیش از source تعیین نشده است}"

if [[ -n "${MAHDIEH_PYTHON:-}" ]]; then
  PYTHON_BIN="${MAHDIEH_PYTHON}"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || {
  echo "مفسر پایتون پیدا نشد (MAHDIEH_PYTHON را تنظیم کنید)." >&2
  exit 1
}

ENV_FILE="${MAHDIEH_ENV_FILE:-${REPO_ROOT}/src/.env}"
ENV_ARGS=()
[[ -f "${ENV_FILE}" ]] && ENV_ARGS=(--env-file "${ENV_FILE}")

DB_DEFAULTS="$(mktemp "${TMPDIR:-/tmp}/mahdieh-db.XXXXXX")"
chmod 600 "${DB_DEFAULTS}"
_DB_TEMP_FILES=("${DB_DEFAULTS}")
db_cleanup() { rm -f "${_DB_TEMP_FILES[@]}"; }
trap db_cleanup EXIT

DB_NAME="$("${PYTHON_BIN}" "${REPO_ROOT}/scripts/db_conf.py" "${ENV_ARGS[@]}" \
           --write-defaults "${DB_DEFAULTS}")" || { echo "خواندن DATABASE_URL شکست خورد." >&2; exit 1; }

db_url_for() {  # db_url_for <name> — نشانی اتصال جنگو به پایگاه‌دادهٔ دیگر
  "${PYTHON_BIN}" "${REPO_ROOT}/scripts/db_conf.py" "${ENV_ARGS[@]}" --url-for-db "$1"
}

# کلاینت‌ها: نام‌های جدید MariaDB، با بازگشت به نام‌های قدیمی سازگار.
MARIADB_BIN="$(command -v mariadb || command -v mysql || true)"
MARIADB_DUMP_BIN="$(command -v mariadb-dump || command -v mysqldump || true)"

db_sql() {  # db_sql <database> <sql> — خروجی جدول‌بندی‌شده با تب، بی‌سرستون
  "${MARIADB_BIN}" --defaults-extra-file="${DB_DEFAULTS}" --batch --skip-column-names \
    --database="$1" -e "$2"
}

run_manage() {  # run_manage <database_url> <log_file> <command> [args…]
  local url="$1" log_file="$2"; shift 2
  ( cd "${REPO_ROOT}/src" && DATABASE_URL="${url}" "${PYTHON_BIN}" manage.py "$@" ) \
    >"${log_file}" 2>&1
}

#: شمارش دقیق ردیف‌های کلیدی — مبنای مقایسهٔ تولید و نسخهٔ بازیابی‌شده.
readonly KEY_COUNTS_SQL="
  SELECT 'audit_records', COUNT(*) FROM audit_auditrecord
  UNION ALL SELECT 'feedback', COUNT(*) FROM reservation_guestfeedback
  UNION ALL SELECT 'migrations', COUNT(*) FROM django_migrations
  UNION ALL SELECT 'outbox_events', COUNT(*) FROM core_outboxevent
  UNION ALL SELECT 'reservations', COUNT(*) FROM reservation_reservation
  UNION ALL SELECT 'sms_deliveries', COUNT(*) FROM notifications_smsdelivery
  UNION ALL SELECT 'triggers', COUNT(*) FROM information_schema.TRIGGERS
                               WHERE TRIGGER_SCHEMA = DATABASE()
  UNION ALL SELECT 'units', COUNT(*) FROM inventory_unit
  UNION ALL SELECT 'users', COUNT(*) FROM identity_user
  UNION ALL SELECT 'waitlist', COUNT(*) FROM waitlist_waitlistentry"
