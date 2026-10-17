#!/usr/bin/env bash
# ── بررسی سطح حملهٔ تصویر ────────────────────────────────────────────────────
# «حذف» بهتر از «نادیده‌گرفتن» است: تصویر پایهٔ پایتون، نسخهٔ خودش از
# pip/setuptools/urllib3/msgpack را در site-packagesِ سیستمی دارد. برنامه هیچ‌وقت
# از آن‌ها استفاده نمی‌کند (همه‌چیز از /app/.venv می‌آید)، ولی هر کدامشان یک
# نقطهٔ ورودِ اضافی و یک یافتهٔ CVEِ بی‌فایده است. Dockerfile آن‌ها را حذف می‌کند
# و این اسکریپت — که در CI داخل خودِ تصویر اجرا می‌شود — ثابت می‌کند:
#   ۱) حذف واقعاً انجام شده (هیچ‌کدام از چهار ماژول در مفسر سیستمی نیست)،
#   ۲) حذف چیزی را نشکسته (جنگو و گانیکورن از venv سالم بالا می‌آیند).
#
# اجرا:  bash scripts/image_attack_surface_check.sh \
#          [--app-python PATH] [--sys-python PATH]
#   --app-python  مفسرِ اجرای برنامه (پیش‌فرض: python، داخل تصویر = /app/.venv)
#   --sys-python  مفسرِ سیستمی تصویر پایه (پیش‌فرض: /usr/local/bin/python3)
# کد خروج: ۰ = سطح حمله پاک · ۱ = یکی از شرط‌ها نقض شد · ۲ = خطای استفاده
set -euo pipefail

APP_PYTHON="python"
SYS_PYTHON="/usr/local/bin/python3"
while [ $# -gt 0 ]; do
  case "$1" in
    --app-python) APP_PYTHON="$2"; shift 2 ;;
    --sys-python) SYS_PYTHON="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "گزینهٔ ناشناخته: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "❌ $*" >&2; exit 1; }

# ۱) مسیر اجرا سالم است — همان چیزی که نباید قربانیِ حذف شود
"$APP_PYTHON" -c "import django, gunicorn" \
  || fail "جنگو/گانیکورن از مفسر اجرا بالا نمی‌آید ($APP_PYTHON) — حذف، مسیر اجرا را شکسته است"
gunicorn_bin="$(dirname "$APP_PYTHON")/gunicorn"
[ -x "$gunicorn_bin" ] || gunicorn_bin="$(command -v gunicorn || true)"
[ -n "$gunicorn_bin" ] && [ -x "$gunicorn_bin" ] \
  || fail "فایل اجرایی gunicorn کنار مفسر اجرا نیست — نقطهٔ ورود تصویر می‌شکند"
"$APP_PYTHON" - <<'PY'
import django, gunicorn
print(f"✅ مسیر اجرا سالم — جنگو {django.get_version()} · گانیکورن {gunicorn.__version__}")
PY

# ۲) و آن چهار ماژول باید از مفسرِ سیستمی غایب باشند
[ -x "$SYS_PYTHON" ] || fail "مفسر سیستمی پیدا نشد: $SYS_PYTHON (با --sys-python بدهید)"

leaked=()
for module in pip setuptools urllib3 msgpack; do
  if "$SYS_PYTHON" -c "import $module" >/dev/null 2>&1; then
    leaked+=("$module")
  fi
done

if [ ${#leaked[@]} -gt 0 ]; then
  fail "این ماژول‌ها هنوز در site-packagesِ سیستمی تصویر هستند: ${leaked[*]}
   رفع: مرحلهٔ «کوچک‌کردن سطح حمله» در Dockerfile را دوباره بسازید."
fi

echo "✅ سطح حملهٔ تصویر پاک است — نه pip، نه setuptools، نه urllib3، نه msgpack سیستمی"
