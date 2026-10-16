#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# آزمون دودی استقرار — «همان کاری که کانتینر می‌کند» را این‌جا تکرار می‌کند.
#
# چرا لازم است؟ «روی سیستم من کار می‌کند» یک ادعا است، نه شاهد. این اسکریپت
# با همان دستور Dockerfile سرور را بالا می‌آورد و شش چیز را می‌سنجد:
#   ۱) سرور با worker درست (uvicorn_worker.UvicornWorker) بالا می‌آید.
#   ۲) /health/live/ و /health/ready/ پاسخ ۲۰۰ می‌دهند.
#   ۳) صفحهٔ اصلی از طریق همان worker سرو می‌شود.
#   ۴) فایل استاتیک با هش و کش یک‌ساله (WhiteNoise روی Manifest) می‌آید.
#   ۵) سرصفحه‌های امنیتی (HSTS/nosniff/Referrer-Policy) روی پاسخ هستند.
#   ۶) SIGTERM باعث خاموشی تمیز (graceful) می‌شود.
#
# اجرا:  bash scripts/deploy_smoke.sh            (پورت پیش‌فرض: ۸۰۱۰)
#        PORT=8020 bash scripts/deploy_smoke.sh
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8010}"
HOST_HEADER="${HOST_HEADER:-mahdieh.local}"
SECRET="${MAHDIEH_SECRET_KEY:-deploy-smoke-only-insecure-key-0123456789-abcdefghij-klmnopqrstuvwxyz}"
PY="${ROOT}/.venv/bin/python"
[[ -x "${PY}" ]] || PY="$(command -v python3)"
LOG="$(mktemp -t mahdieh-deploy-smoke-XXXXXX.log)"
FAILED=0

note() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAILED=1; }

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill -TERM "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# نگهبان پورت: اگر سروری از قبل روی این پورت گوش می‌دهد، آزمون دودی بی‌خبر
# نتیجهٔ *آن* سرور را می‌سنجد (خطای واقعی که یک بار افتاد: آزمون سبز شد در حالی
# که سرور تازه بالا نیامده بود). پس اول پورت را چک می‌کنیم.
if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/health/live/" 2>/dev/null; then
  printf '  \033[31m✗\033[0m پورت %s اشغال است؛ آزمون دودی نتیجهٔ سرور دیگری را می‌سنجد.\n' "${PORT}"
  printf '     سرور قدیمی را ببندید یا PORT=8020 bash scripts/deploy_smoke.sh بزنید.\n'
  exit 2
fi

note "۰) آماده‌سازی استاتیک (منیفست هش‌دار، همان‌طور که در تصویر انجام می‌شود)"
( cd "${ROOT}/src" && DJANGO_SETTINGS_MODULE=config.settings.production \
    MAHDIEH_SECRET_KEY="${SECRET}" MAHDIEH_ALLOWED_HOSTS="${HOST_HEADER},127.0.0.1,localhost" \
    DATABASE_PASSWORD="${DATABASE_PASSWORD:-mahdieh}" \
    "${PY}" manage.py collectstatic --noinput --clear ) >"${LOG}" 2>&1 \
  && ok "collectstatic انجام شد" || { bad "collectstatic شکست خورد"; tail -20 "${LOG}"; exit 1; }

note "۱) بالا آوردن سرور با همان دستور کانتینر"
( cd "${ROOT}/src" && DJANGO_SETTINGS_MODULE=config.settings.production \
    MAHDIEH_SECRET_KEY="${SECRET}" MAHDIEH_ALLOWED_HOSTS="${HOST_HEADER},127.0.0.1,localhost" \
    DATABASE_PASSWORD="${DATABASE_PASSWORD:-mahdieh}" \
    exec "${ROOT}/.venv/bin/gunicorn" config.asgi:application \
      -k uvicorn_worker.UvicornWorker -b "0.0.0.0:${PORT}" -w 2 \
      --timeout 30 --graceful-timeout 10 --access-logfile - ) >>"${LOG}" 2>&1 &
SERVER_PID=$!

READY=0
for _ in $(seq 1 40); do
  if curl -fsS -o /dev/null -H "Host: ${HOST_HEADER}" -H 'X-Forwarded-Proto: https' \
       "http://127.0.0.1:${PORT}/health/live/"; then READY=1; break; fi
  sleep 0.5
done
[[ "${READY}" == "1" ]] || { bad "سرور در ۲۰ ثانیه پاسخ نداد"; tail -20 "${LOG}"; exit 1; }

grep -q "uvicorn_worker.UvicornWorker" "${LOG}" && ok "worker = uvicorn_worker.UvicornWorker" || bad "worker درست بالا نیامد"
grep -q E[0-9][0-9][0-9][0-9] "${LOG}" && bad "خطای جنگو در لاگ سرور" || ok "بدون خطای جنگو در لاگ"

note "۲) نقطه‌های سلامت"
for path in /health/live/ /health/ready/; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${HOST_HEADER}" \
          -H 'X-Forwarded-Proto: https' "http://127.0.0.1:${PORT}${path}")"
  [[ "${code}" == "200" ]] && ok "${path} ⇒ ۲۰۰" || bad "${path} ⇒ ${code}"
done

note "۳) صفحهٔ اصلی و سرصفحه‌های امنیتی"
HEADERS="$(curl -s -D - -o /dev/null -H "Host: ${HOST_HEADER}" -H 'X-Forwarded-Proto: https' \
           "http://127.0.0.1:${PORT}/")"
grep -qi '^strict-transport-security' <<<"${HEADERS}" && ok "HSTS" || bad "HSTS نیست"
grep -qi '^x-content-type-options: nosniff' <<<"${HEADERS}" && ok "nosniff" || bad "nosniff نیست"
grep -qi '^referrer-policy' <<<"${HEADERS}" && ok "Referrer-Policy" || bad "Referrer-Policy نیست"
grep -qi '^content-security-policy\|^content-security-policy-report-only' <<<"${HEADERS}" \
  && ok "CSP" || bad "CSP نیست"

note "۴) استاتیک با هش و کش بلندمدت (WhiteNoise + ManifestStaticFilesStorage)"
ASSET="$(cd "${ROOT}/src" && "${PY}" - <<'PYEOF'
import json
from pathlib import Path
manifest = json.loads((Path("staticfiles/staticfiles.json")).read_text(encoding="utf-8"))
for key, value in manifest["paths"].items():
    if key.endswith(".css"):
        print(value)
        break
PYEOF
)"
if [[ -n "${ASSET}" ]]; then
  # سرصفحهٔ X-Forwarded-Proto لازم است: در تولید، HTTPS اجبار شده است و
  # درخواست بدون آن با ۳۰۱ ریدایرکت می‌شود (همان‌طور که nginx می‌فرستد).
  ASSET_HEADERS="$(curl -s -D - -o /dev/null -H "Host: ${HOST_HEADER}" \
                    -H 'X-Forwarded-Proto: https' \
                    "http://127.0.0.1:${PORT}/static/${ASSET}")"
  grep -qi '^cache-control:.*max-age=31536000' <<<"${ASSET_HEADERS}" \
    && ok "کش یک‌ساله روی ${ASSET}" || bad "سرصفحهٔ کش نادرست روی ${ASSET}"
  [[ "${ASSET}" =~ \.[0-9a-f]{12}\. ]] && ok "نام فایل هش‌دار است" || bad "نام فایل هش ندارد"
else
  bad "منیفست استاتیک خالی است"
fi

note "۵) خاموشی تمیز با SIGTERM"
START_MS="$(date +%s%3N)"
kill -TERM "${SERVER_PID}"
if timeout 10 bash -c "while kill -0 ${SERVER_PID} 2>/dev/null; do sleep 0.2; done"; then
  ELAPSED=$(( $(date +%s%3N) - START_MS ))
  ok "سرور در ${ELAPSED} میلی‌ثانیه تمیز خاموش شد"
else
  bad "سرور با SIGTERM در ۱۰ ثانیه خاموش نشد"
fi
SERVER_PID=""

printf '\n'
if [[ "${FAILED}" == "0" ]]; then
  printf '\033[32mآزمون دودی استقرار: موفق ✅\033[0m\n'
else
  printf '\033[31mآزمون دودی استقرار: ناموفق ❌ — لاگ: %s\033[0m\n' "${LOG}"
  tail -30 "${LOG}"
fi
exit "${FAILED}"
