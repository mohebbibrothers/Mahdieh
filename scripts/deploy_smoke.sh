#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# آزمون دودِ استقرار (Deploy smoke) — همان دستوری که در Dockerfile اجرا می‌شود،
# این‌جا واقعاً بالا می‌آید و بررسی می‌شود.
#
# چرا لازم است؟ چون «قرارداد استقرار» جداست از «کد اپلیکیشن»: ممکن است همهٔ
# تست‌ها سبز باشند ولی ایمیجِ تولیدی بالا نیاید (وابستگی گمشده، کارگر اشتباه،
# تنظیم مرده). این اسکریپت همان شکاف را می‌بندد:
#   ۱) collectstatic با Manifest/WhiteNoise ⇒ نسخه‌بندی دارایی‌ها
#   ۲) بالا آوردن gunicorn + uvicorn_worker روی تنظیمات **production**
#   ۳) بررسی /health/live/ و /health/ready/ با هدر X-Forwarded-Proto: https
#   ۴) بررسی سرو دارایی ایستای نسخه‌دار و هدرهای امنیتی
#   ۵) خاموشی آرام (graceful) و تأیید
#
# استفاده:
#   scripts/deploy_smoke.sh              # پورت ۸۰۱۰
#   MAHDIEH_SMOKE_PORT=8021 scripts/deploy_smoke.sh
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

: "${MAHDIEH_SMOKE_PORT:=8010}"
: "${MAHDIEH_SMOKE_HOST:=mahdieh.local}"
: "${DATABASE_NAME:=mahdieh}"
: "${DATABASE_USER:=mahdieh}"
: "${DATABASE_PASSWORD:=mahdieh}"
: "${DATABASE_HOST:=127.0.0.1}"
: "${DATABASE_PORT:=5432}"

# رازِ بوت‌استرپِ فقط-آزمایشی: در تولید واقعی از مخزن رازها می‌آید، نه از این‌جا.
: "${MAHDIEH_SECRET_KEY:=deploy-smoke-only-insecure-key-0123456789-abcdefghij-klmnopqrstuvwxyz}"

BASE_URL="http://127.0.0.1:${MAHDIEH_SMOKE_PORT}"
LOG_FILE="$(mktemp -t mahdieh-smoke-XXXXXX.log)"
PID_FILE="$(mktemp -t mahdieh-smoke-XXXXXX.pid)"
FAILURES=0

log()  { printf '%s [deploy-smoke] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
fail() { printf '%s [deploy-smoke][❌] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; FAILURES=$((FAILURES + 1)); }
pass() { printf '%s [deploy-smoke][✅] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }

cleanup() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid; pid="$(cat "${PID_FILE}")"
    kill -TERM "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.2; done
    kill -KILL "${pid}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
}
trap cleanup EXIT

export DJANGO_SETTINGS_MODULE=config.settings.production
export MAHDIEH_ALLOWED_HOSTS="${MAHDIEH_SMOKE_HOST},127.0.0.1,localhost"
export MAHDIEH_SECRET_KEY
export DATABASE_PASSWORD DATABASE_NAME DATABASE_USER DATABASE_HOST DATABASE_PORT
export PATH="${REPO_ROOT}/.venv/bin:${PATH}"

cd "${REPO_ROOT}/src"

# ── ۱) ساخت دارایی‌های نسخه‌دار ──────────────────────────────────────────────
log "collectstatic با انبار فشردهٔ نسخه‌دار…"
if python manage.py collectstatic --noinput --clear >/tmp/collectstatic.log 2>&1; then
  pass "دارایی‌های ایستا ساخته شد ($(find "${REPO_ROOT}/src/staticfiles" -type f | wc -l) فایل)"
else
  fail "collectstatic شکست خورد"; tail -5 /tmp/collectstatic.log
fi
if [[ -f "${REPO_ROOT}/src/staticfiles/staticfiles.json" ]]; then
  pass "مانیفست نسخه‌بندی موجود است (staticfiles.json)"
else
  fail "مانیفست staticfiles.json ساخته نشد — نسخه‌بندی دارایی‌ها کار نمی‌کند"
fi

# ── ۲) بالا آوردن کارگر، دقیقاً مثل Dockerfile ──────────────────────────────
log "اجرای gunicorn + uvicorn_worker (تنظیمات production) روی پورت ${MAHDIEH_SMOKE_PORT}…"
gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker \
  -b "127.0.0.1:${MAHDIEH_SMOKE_PORT}" -w 2 --timeout 60 --graceful-timeout 10 \
  >"${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"

READY=0
for _ in $(seq 1 60); do
  if curl -fsS -H "Host: ${MAHDIEH_SMOKE_HOST}" -H "X-Forwarded-Proto: https" \
       "${BASE_URL}/health/live/" >/dev/null 2>&1; then READY=1; break; fi
  sleep 0.5
done
if ((READY)); then
  pass "کارگر ASGI بالا آمد و /health/live/ پاسخ داد"
else
  fail "کارگر بالا نیامد"; tail -20 "${LOG_FILE}"
  exit 1
fi

request() {  # request <path> [expected_status]
  local path="$1" expected="${2:-200}" status
  status="$(curl -sS -o /tmp/smoke_body -D /tmp/smoke_headers -w '%{http_code}' \
              -H "Host: ${MAHDIEH_SMOKE_HOST}" -H "X-Forwarded-Proto: https" \
              "${BASE_URL}${path}")"
  if [[ "${status}" == "${expected}" ]]; then
    pass "${path} ⇒ ${status}"
  else
    fail "${path} ⇒ ${status} (انتظار ${expected})"
  fi
}

# ── ۳) مسیرهای حیاتی ────────────────────────────────────────────────────────
request "/health/live/" 200
request "/health/ready/" 200
request "/" 200
request "/units/" 200
request "/board/" 302                 # ناشناس ⇒ هدایت به ورود
request "/django-admin/" 302          # مدیریت Django روی مسیر غیرمعمول است
request "/admin/" 404                 # و مسیر آشنا **بسته** است (ضد اسکنرهای خودکار)
request "/.env" 404
request "/wp-login.php" 404

# ── ۴) دارایی ایستای نسخه‌دار از راه وایت‌نویز ──────────────────────────────
STATIC_PATH="$(python - <<'PY'
import json, pathlib
manifest = pathlib.Path("staticfiles/staticfiles.json")
data = json.loads(manifest.read_text(encoding="utf-8"))
css = [v for k, v in data["paths"].items() if k.endswith("mahdieh.css")]
print(f"/static/{css[0]}" if css else "")
PY
)"
if [[ -n "${STATIC_PATH}" ]]; then
  request "${STATIC_PATH}" 200
  if grep -qi "cache-control:.*max-age=31536000" /tmp/smoke_headers; then
    pass "دارایی نسخه‌دار با Cache-Control یک‌ساله سرو می‌شود"
  else
    fail "هدر کش دارایی ایستا نامناسب است: $(grep -i cache-control /tmp/smoke_headers || echo 'غایب')"
  fi
  if grep -qi "content-encoding: br\|content-encoding: gzip" /tmp/smoke_headers; then
    pass "دارایی فشرده سرو می‌شود ($(grep -i content-encoding /tmp/smoke_headers | tr -d '\r'))"
  else
    # curl فشرده‌سازی را پیش‌فرض درخواست نمی‌کند؛ این‌بار صریح درخواست می‌کنیم.
    curl -sS --compressed -o /dev/null -D /tmp/smoke_gz -H "Host: ${MAHDIEH_SMOKE_HOST}" \
         -H "X-Forwarded-Proto: https" "${BASE_URL}${STATIC_PATH}"
    if grep -qi "content-encoding: br\|content-encoding: gzip" /tmp/smoke_gz; then
      pass "دارایی فشرده سرو می‌شود ($(grep -i content-encoding /tmp/smoke_gz | tr -d '\r'))"
    else
      fail "دارایی ایستا فشرده سرو نمی‌شود — پهنای باند هدر می‌رود"
    fi
  fi
else
  fail "مسیر دارایی نسخه‌دار در مانیفست پیدا نشد"
fi

# ── ۵) هدرهای امنیتی روی پاسخ html ──────────────────────────────────────────
request "/" 200
headers="$(curl -sS -D - -o /dev/null -H "Host: ${MAHDIEH_SMOKE_HOST}" \
            -H "X-Forwarded-Proto: https" "${BASE_URL}/")"
for expected_header in \
    "strict-transport-security: max-age=31536000" \
    "x-content-type-options: nosniff" \
    "referrer-policy: strict-origin-when-cross-origin" \
    "content-security-policy:" ; do
  if grep -qi -- "^${expected_header}" <<<"${headers}"; then
    pass "هدر امنیتی موجود است: ${expected_header%%:*}"
  else
    fail "هدر امنیتی غایب: ${expected_header%%:*}"
  fi
done

# ── ۶) انتقال اجباری HTTPS بدون هدر پروکسی ──────────────────────────────────
status="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: ${MAHDIEH_SMOKE_HOST}" "${BASE_URL}/")"
if [[ "${status}" == "301" ]]; then
  pass "درخواست بدون TLS به HTTPS هدایت می‌شود (301)"
else
  fail "انتقال اجباری HTTPS کار نمی‌کند (وضعیت ${status})"
fi

# ── ۷) میزبان غیرمجاز باید رد شود ───────────────────────────────────────────
status="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: evil.example.com" \
            -H "X-Forwarded-Proto: https" "${BASE_URL}/")"
if [[ "${status}" == "400" ]]; then
  pass "میزبان اعلام‌نشده رد می‌شود (400)"
else
  fail "محافظت ALLOWED_HOSTS کار نمی‌کند (وضعیت ${status})"
fi

# ── ۸) خاموشی آرام ──────────────────────────────────────────────────────────
log "ارسال SIGTERM و بررسی خاموشی آرام…"
pid="$(cat "${PID_FILE}")"
kill -TERM "${pid}"
GRACEFUL=0
for _ in $(seq 1 50); do kill -0 "${pid}" 2>/dev/null || { GRACEFUL=1; break; }; sleep 0.2; done
if ((GRACEFUL)); then
  pass "کارگر با SIGTERM در کمتر از ۱۰ ثانیه و بی‌خطا خاموش شد"
else
  fail "خاموشی آرام کارگر انجام نشد"
fi

echo
if ((FAILURES == 0)); then
  log "آزمون دودِ استقرار: موفق ✅"
else
  log "آزمون دودِ استقرار: ${FAILURES} یافتهٔ ناموفق ❌"
  exit 1
fi
