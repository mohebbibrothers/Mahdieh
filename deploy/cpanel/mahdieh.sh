#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# mahdieh — فرمان یکپارچهٔ مدیریت مهدیه روی میزبان (پوستهٔ نازک؛ منطق در mahdieh_cli.py)
#   mahdieh update · mahdieh · mahdieh doctor · mahdieh logs · mahdieh rollback · mahdieh help
# deploy.sh آن را در ‎~/mahdieh/bin نصب و به PATH ‏(‎~/.bashrc) می‌افزاید.
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
exec "${MAHDIEH_BASE_PYTHON:-/opt/alt/python312/bin/python3}" "${here}/mahdieh_cli.py" "$@"
