# ══════════════════════════════════════════════════════════════════════════════
# ایمیج تولیدی مهدیه — چندمرحله‌ای، غیر-root، با کاربر و فایل‌سیستم امن
# ══════════════════════════════════════════════════════════════════════════════
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev gettext \
    && rm -rf /var/lib/apt/lists/*

# وابستگی‌ها پیش از کد کپی می‌شوند تا لایهٔ کش مؤثر باشد
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev && uv run python src/manage.py compilemessages || true

# ── مرحلهٔ اجرا ───────────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    # سقف درخواست‌های هم‌زمانِ هر کارگر (سدِ ظرفیت ASGI). با استخر پایگاه‌داده
    # هم‌خوان است: max_concurrency × کارگرها نباید از بودجهٔ اتصال بگذرد.
    LOAD_SHEDDING_MAX_CONCURRENCY=120 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 mahdieh \
    && useradd --system --uid 1001 --gid mahdieh --create-home mahdieh

WORKDIR /app
COPY --from=builder --chown=mahdieh:mahdieh /app /app

# دارایی‌های ایستا با کاربر غیر-root جمع می‌شوند
RUN mkdir -p /app/staticfiles /app/media && chown -R mahdieh:mahdieh /app/staticfiles /app/media

USER mahdieh
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health/live/ || exit 1

# ASGI پیش‌فرض است (async-capable)؛ تعداد کارگر از محیط قابل تنظیم
# --timeout 30: سنجش بار فاز ۸ نشان داد p99 = ۱٫۵ ثانیه است؛ پس ۳۰ ثانیه سقف
# با حاشیهٔ بیست‌برابری، فقط درخواست‌های واقعاً قفل‌شده را می‌کشد.
# --max-requests: بازیافت دوره‌ای کارگر، نشت آرام حافظه/اتصال را می‌بندد
# (با ۳ کارگر و jitter، هیچ زائری این بازیافت را حس نمی‌کند).
CMD ["sh", "-c", "gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker -b 0.0.0.0:8000 -w ${WEB_CONCURRENCY:-3} --timeout 30 --graceful-timeout 30 --max-requests 2000 --max-requests-jitter 200 --access-logfile -"]
