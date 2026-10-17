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
# ⚠️ درسِ گران: `uv run` پیش از اجرا، محیط را «همگام» می‌کند و به‌طور پیش‌فرض
# گروه dev را هم نصب می‌کند. نتیجه: تصویری که با ۵۷ بسته ساخته شده بود، بی‌صدا
# به ~۱۳۰ بسته رسید (pytest، pip-audit، mypy، ruff، faker…) — هم حجم، هم سطح
# حمله، هم یافتهٔ CVE در ابزارِ pip. پس sync و run را از هم جدا می‌کنیم.
RUN uv sync --frozen --no-dev && uv run --no-sync python src/manage.py compilemessages || true

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

# ── کوچک‌کردن سطح حمله: ابزارِ مدیریت بستهٔ تصویر پایه ────────────────────────
# تصویر پایه نسخه‌های خودش از pip/setuptools/urllib3/msgpack را در
# site-packagesِ سیستمی دارد. برنامه هیچ‌وقت از آن‌ها استفاده نمی‌کند: همه‌چیز از
# /app/.venv می‌آید و PATH هم همان را اول می‌گذارد. نگه‌داشتن‌شان هیچ فایدهٔ
# اجرایی ندارد و فقط یافتهٔ CVE می‌سازد، پس حذف می‌شوند — «حذف» بهتر از
# «نادیده‌گرفتن». اسکن تصویر در CI (دروازهٔ واقعی) همین را شرط می‌کند.
# چرا **کل** site-packages سیستمی و نه فقط چند بسته؟ تجربهٔ نخست نشان داد حذفِ
# نقطه‌ای، «متادیتای» آن بسته‌ها را جا می‌گذارد و اسکنر همچنان آن‌ها را می‌بیند
# (یافتهٔ تصویر: همان ۴ مورد، در حالی که آزمون import ثابت می‌کرد کدشان در دسترس
# نیست). مسیر اجرا هیچ‌چیز از این پوشه نمی‌خواهد: کتابخانهٔ استاندارد جای دیگری
# است و وابستگی‌های برنامه همه در /app/.venv. پس پوشه خالی می‌شود و اسکنر و
# واقعیت، یک چیز می‌گویند.
RUN set -eu; \
    site="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    rm -rf "$site"/* "$site"/.[!.]* 2>/dev/null || true; \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13; \
    python3 -c "import sysconfig, pathlib; p = pathlib.Path(sysconfig.get_paths()['purelib']); left = [x.name for x in p.iterdir()]; assert not left, left"

WORKDIR /app
COPY --from=builder --chown=mahdieh:mahdieh /app /app
# نقطهٔ ورود، همان اسکریپت مخزن است: انتظار دیتابیس ⇒ مهاجرت ⇒ static ⇒ check --deploy
# ⇒ سپس فرمان CMD. بدون این خط، مهاجرت‌ها در استقرار کانتینری جا می‌مانند و
# «استقرار موفق ولی سامانهٔ نیمه‌آماده» رخ می‌دهد.
RUN chmod +x /app/entrypoint.sh

# دارایی‌های ایستا با کاربر غیر-root جمع می‌شوند
RUN mkdir -p /app/staticfiles /app/media && chown -R mahdieh:mahdieh /app/staticfiles /app/media

USER mahdieh
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health/live/ || exit 1

# ASGI پیش‌فرض است (async-capable)؛ تعداد کارگر از محیط قابل تنظیم
# --timeout 30: سنجش بار فاز ۸ نشان داد p99 = ۱٫۵ ثانیه است؛ پس ۳۰ ثانیه سقف
# با حاشیهٔ بیست‌برابری، فقط درخواست‌های واقعاً قفل‌شده را می‌کشد.
# --max-requests: بازیافت دوره‌ای کارگر، نشت آرام حافظه/اتصال را می‌بندد
# (با ۳ کارگر و jitter، هیچ زائری این بازیافت را حس نمی‌کند).
# کارگرِ اجرا: WSGI هم‌زمان (`sync`).
# چرا ASGI-uvicorn نه؟ در بارسنجی فاز ۱۱ روی همان ماشین و همان لحظه، p95 در
# ۶۴ کاربر هم‌زمان با ASGI ۱۸۹۰ms و با WSGI ۱۲۹۲ms بود (ADR-0015). بارِ این
# سامانه هم‌زمانِ خالص است (قالب‌سازی + ORM) و حلقهٔ رویداد در آن فقط سربار
# می‌سازد. مسیر ASGI (`config.asgi`) دست‌نخورده و سدِ ظرفیت‌دار مانده است؛ اگر
# روزی بلادرنگ (SSE) لازم شد، با یک خط تغییر فعال می‌شود.
CMD ["sh", "-c", "gunicorn config.wsgi:application -k sync -b 0.0.0.0:8000 -w ${WEB_CONCURRENCY:-5} --timeout 30 --graceful-timeout 30 --max-requests 2000 --max-requests-jitter 200 --access-logfile -"]
