#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# پشتیبان‌گیری پایگاه‌دادهٔ مهدیه — «پشتیبانِ تأییدشده»، نه فقط پشتیبان.
#
# فلسفه: پشتیبانی که بازیابی نشده باشد، پشتیبان نیست؛ یک آرزو است.
# این اسکریپت سه کار می‌کند:
#   ۱) pg_dump به شکل custom (فشرده، قابل بازیابی انتخابی)
#   ۲) هش SHA-256 برای تشخیص خرابی/دست‌کاری فایل
#   ۳) صحت‌سنجی فایل با `pg_restore --list` تا مطمئن شویم آرشیو سالم است
# و در پایان یک «بیانیهٔ پشتیبان» (manifest JSON) می‌نویسد که در بازیابی
# بررسی می‌شود — پس هیچ‌وقت نمی‌فهمیم پشتیبان خراب بوده *بعد* از حادثه.
#
# استفاده:
#   scripts/db_backup.sh                     # پشتیبان کامل در var/backups
#   MAHDIEH_BACKUP_DIR=/mnt/backups scripts/db_backup.sh
#   scripts/db_backup.sh --no-verify         # فقط در شرایط اضطراری
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

: "${MAHDIEH_BACKUP_DIR:=${REPO_ROOT}/var/backups}"
: "${MAHDIEH_RETENTION_DAYS:=14}"
: "${MAHDIEH_PREFIX:=mahdieh}"

VERIFY=1
LABEL="${MAHDIEH_BACKUP_LABEL:-manual}"

log()  { printf '%s [backup] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die()  { printf '%s [backup][خطا] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --no-verify) VERIFY=0; shift ;;
    --label) LABEL="${2:?--label به یک مقدار نیاز دارد}"; shift 2 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

# ── واکشی تنظیمات اتصال: از DATABASE_URL یا از متغیرهای استاندارد Postgres ──
if [[ -n "${DATABASE_URL:-}" ]]; then
  export PGURL="${DATABASE_URL}"
else
  : "${DATABASE_HOST:=127.0.0.1}"
  : "${DATABASE_PORT:=5432}"
  : "${DATABASE_NAME:=mahdieh}"
  : "${DATABASE_USER:=mahdieh}"
  : "${DATABASE_PASSWORD:=}"
  export PGPASSWORD="${DATABASE_PASSWORD}"
  export PGURL="postgresql://${DATABASE_USER}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}"
fi

for binary in pg_dump pg_restore sha256sum; do
  command -v "${binary}" >/dev/null 2>&1 || die "${binary} روی این میزبان نصب نیست."
done

# ── نام فایل با مُهر زمانی UTC + برچسب (برای تفکیک پشتیبان دستی/زمان‌بند) ──
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly RELEASE="${MAHDIEH_RELEASE:-$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo 'unknown')}"
mkdir -p "${MAHDIEH_BACKUP_DIR}"
readonly BASENAME="${MAHDIEH_PREFIX}-${STAMP}-${LABEL}"
readonly DUMP_PATH="${MAHDIEH_BACKUP_DIR}/${BASENAME}.dump"
readonly MANIFEST_PATH="${MAHDIEH_BACKUP_DIR}/${BASENAME}.dump.json"

log "شروع پشتیبان‌گیری از ${PGURL%%\?*} (نسخهٔ کد: ${RELEASE})"

# ── ۱) خودِ پشتیبان. --no-owner تا بازیابی روی خوشهٔ دیگر هم بی‌دردسر باشد ──
pg_dump --format=custom --compress=6 --no-owner --no-privileges \
        --dbname="${PGURL}" --file="${DUMP_PATH}" \
  || die "pg_dump شکست خورد."

readonly SIZE_BYTES="$(stat -c '%s' "${DUMP_PATH}")"
readonly SHA="$(sha256sum "${DUMP_PATH}" | awk '{print $1}')"
log "آرشیو ساخته شد: $(basename "${DUMP_PATH}") ($(numfmt --to=iec "${SIZE_BYTES}" 2>/dev/null || echo "${SIZE_BYTES} بایت"))"
log "SHA-256: ${SHA}"

# ── ۲) صحت‌سنجی ساختاری: اگر pg_restore نتواند فهرست محتوا را بخواند، فایل مرده است ──
TABLES=""
if ((VERIFY)); then
  TABLES_COUNT="$(pg_restore --list "${DUMP_PATH}" | grep -c 'TABLE DATA' || true)"
  [[ "${TABLES_COUNT}" -gt 0 ]] || die "آرشیو هیچ جدولی ندارد — پشتیبان بی‌ارزش است."
  TABLES="$(pg_restore --list "${DUMP_PATH}" | awk '/TABLE DATA/ {print $NF}' | sort | paste -sd, -)"
  log "صحت ساختاری تأیید شد: ${TABLES_COUNT} جدول با داده."
else
  log "هشدار: صحت‌سنجی با --no-verify غیرفعال شد (فقط در اضطرار)."
fi

# ── ۳) بیانیهٔ پشتیبان: بازیابی، این فایل را می‌خواند و هش را بررسی می‌کند ──
python3 - "${MANIFEST_PATH}" "${BASENAME}" "${SIZE_BYTES}" "${SHA}" "${RELEASE}" "${VERIFY}" "${TABLES}" <<'PY'
import json, sys

manifest_path, name, size, sha, release, verified, tables = sys.argv[1:8]
payload = {
    "schema_version": 1,
    "name": name,
    "file": name + ".dump",
    "created_at": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat(),
    "size_bytes": int(size),
    "sha256": sha,
    "release": release,
    "structurally_verified": bool(int(verified)),
    "tables": [t for t in tables.split(",") if t],
    "restore_command": (
        f"scripts/db_restore.sh --file var/backups/{name}.dump "
        f"--expect-sha256 {sha}"
    ),
}
with open(manifest_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
log "بیانیهٔ پشتیبان نوشته شد: $(basename "${MANIFEST_PATH}")"

# ── ۴) چرخش پشتیبان‌های کهنه (پیش‌فرض ۱۴ روز) ──
DELETED=0
while IFS= read -r stale; do
  [[ -n "${stale}" ]] || continue
  rm -f "${stale}" "${stale%.dump}.dump.json"
  DELETED=$((DELETED + 1))
done < <(find "${MAHDIEH_BACKUP_DIR}" -maxdepth 1 -name "${MAHDIEH_PREFIX}-*.dump" \
           -mtime "+${MAHDIEH_RETENTION_DAYS}" -print)
((DELETED > 0)) && log "چرخش نگه‌داری: ${DELETED} پشتیبان قدیمی حذف شد."
log "تمام. مسیر: ${DUMP_PATH}"
