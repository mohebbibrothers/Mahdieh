"""تست «قرارداد استقرار» — قفلِ زنجیرهٔ کد تا کانتینر تا کارگر.

چرا این فایل وجود دارد؟ چون بارسنجی فاز ۸ دو شکاف واقعی را لو داد که هیچ
تست واحدی نمی‌گرفت:
    ۱) ``Dockerfile`` دستور ``gunicorn … uvicorn_worker`` را اجرا می‌کرد، ولی
       هیچ‌کدام از این دو بسته در ``pyproject.toml`` نبود ⇒ ایمیج تولیدی در زمان
       راه‌اندازی می‌مُرد. (تست‌ها سبز بودند، استقرار شکسته.)
    ۲) ``STATICFILES_STORAGE`` در Django 6.1 **حذف** شده است و تعیین آن بی‌صدا
       بی‌اثر می‌ماند ⇒ نسخه‌بندی دارایی‌ها از کار می‌افتاد.

درس: «سبز بودن تست‌های اپ» با «قابل‌استقرار بودن سامانه» یکی نیست. این تست‌ها
همان فاصله را می‌بندند: هر چیزی که در Dockerfile یا تنظیمات تولید به آن تکیه
می‌کنیم، این‌جا *اجرا* و *اثبات* می‌شود.
"""

from __future__ import annotations

import importlib
from importlib.util import find_spec
import pathlib
import re
import subprocess
import sys
import tempfile

from django.conf import settings
from django.urls import Resolver404, resolve
import pytest

REPO_ROOT = pathlib.Path(settings.BASE_DIR).parent
ROOT = REPO_ROOT
DOCKERFILE = REPO_ROOT / "Dockerfile"
PYPROJECT = REPO_ROOT / "pyproject.toml"

SECRET_KEY_FOR_TEST = "x" * 60


@pytest.fixture
def production_settings(monkeypatch: pytest.MonkeyPatch):
    """تنظیمات تولید را با محیط کامل بارگذاری می‌کند (fail-fast را نگه می‌دارد)."""
    monkeypatch.setenv("MAHDIEH_SECRET_KEY", SECRET_KEY_FOR_TEST)
    monkeypatch.setenv("MAHDIEH_ALLOWED_HOSTS", "mahdieh.ir,www.mahdieh.ir")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret")
    module = importlib.import_module("config.settings.production")
    return importlib.reload(module)


# ─────────────────────── وابستگی‌های اجرا (Dockerfile) ───────────────────────
def test_server_packages_are_declared_dependencies() -> None:
    """gunicorn/uvicorn-worker/whitenoise باید وابستگی رسمی پروژه باشند."""
    project = PYPROJECT.read_text(encoding="utf-8")
    for package in ("gunicorn", "uvicorn", "uvicorn-worker", "whitenoise"):
        assert package in project, f"بستهٔ «{package}» در pyproject.toml اعلام نشده است."


def test_server_packages_are_importable_in_current_environment() -> None:
    """و همان بسته‌ها باید در محیط نصب واقعاً قابل import باشند."""
    for module in ("gunicorn", "uvicorn_worker", "whitenoise.middleware", "whitenoise.storage"):
        assert find_spec(module) is not None, f"ماژول «{module}» نصب نیست."


#: نام کارگرهای توکارِ gunicorn (بدون مسیر ماژول) — با `-k sync` می‌آیند.
BUILTIN_GUNICORN_WORKERS = frozenset({"sync", "gthread", "gevent", "eventlet", "tornado"})


def test_dockerfile_worker_class_exists() -> None:
    """کارگرِ اعلام‌شده در دستور اجرای تصویر باید واقعاً وجود داشته باشد.

    دو حالت درست دارد: نام توکار gunicorn (`sync`) یا مسیر ماژول (`pkg.Class`).
    حالت دوم واقعاً import می‌شود تا «کلاسِ نام‌وجود» لو برود.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"-k\s+([\w.]+)", dockerfile)
    assert match is not None, "دستور اجرای Dockerfile کارگر (‎-k) ندارد."
    worker = match.group(1)
    if worker in BUILTIN_GUNICORN_WORKERS:
        return
    module_path, _, attribute = worker.rpartition(".")
    module = importlib.import_module(module_path)
    assert hasattr(module, attribute), f"کارگر «{worker}» پیدا نشد."


@pytest.mark.parametrize("artifact", ["Dockerfile", "deploy/systemd/mahdieh.service"])
def test_serving_artifacts_use_the_decided_entrypoint(artifact: str) -> None:
    """تصویر و unit باید همان نقطهٔ ورودیِ تصمیم‌شده (WSGI) را اجرا کنند.

    دلیل تصمیم در ADR-0015 است؛ این آزمون جلوی «واگراییِ خاموش» را می‌گیرد:
    روزی که کسی در یک فایل به ASGI برگردد، این‌جا قرمز می‌شود.
    """
    text = (ROOT / artifact).read_text(encoding="utf-8")
    assert "config.wsgi:application" in text, f"{artifact} نقطهٔ ورود WSGI را اجرا نمی‌کند."
    assert "-k sync" in text or "--worker-class sync" in text, (
        f"{artifact} کارگر هم‌زمان (sync) را اجرا نمی‌کند."
    )


def test_wsgi_entrypoint_is_importable_and_callable() -> None:
    """نقطهٔ ورود WSGI باید واقعاً وجود داشته باشد (نه فقط در سند)."""
    from config.wsgi import application

    assert callable(application)


def test_dockerfile_healthcheck_target_is_a_real_route() -> None:
    """مسیر HEALTHCHECK باید مسیر واقعی سامانه باشد، نه رشتهٔ حدسی."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"HEALTHCHECK[^\n]*\n?[^\n]*?(/health/[\w/]+)", dockerfile)
    assert match is not None, "Dockerfile مسیر سلامت ندارد."
    try:
        resolve(match.group(1))
    except Resolver404:  # pragma: no cover - فقط اگر مسیر تغییر کند
        pytest.fail(f"مسیر سلامت «{match.group(1)}» در سامانه وجود ندارد.")


def test_dockerfile_keeps_capacity_env_knobs() -> None:
    """شمار کارگر و سقف ظرفیت باید از محیط قابل تنظیم باشند (مقیاس‌پذیری)."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "${WEB_CONCURRENCY" in dockerfile
    assert "LOAD_SHEDDING_MAX_CONCURRENCY" in dockerfile


# ──────────────────────────── تنظیمات تولید ────────────────────────────────
def test_static_files_use_hashed_manifest_storage(production_settings) -> None:
    """انبار دارایی ایستا باید Manifest/WhiteNoise باشد (کلید درست: STORAGES)."""
    backend = production_settings.STORAGES["staticfiles"]["BACKEND"]
    assert "ManifestStaticFilesStorage" in backend
    assert not hasattr(production_settings, "STATICFILES_STORAGE"), (
        "تنظیم حذف‌شدهٔ STATICFILES_STORAGE در Django 6 بی‌اثر است؛ باید از STORAGES استفاده شود."
    )


def test_whitenoise_serves_static_behind_security_middleware(production_settings) -> None:
    """وایت‌نویز باید پس از لایهٔ امنیتی و پیش از بقیه بنشیند."""
    middleware = list(production_settings.MIDDLEWARE)
    index = middleware.index("whitenoise.middleware.WhiteNoiseMiddleware")
    assert middleware[index - 1] == "django.middleware.security.SecurityMiddleware"
    assert middleware[0] == "apps.core.middleware.RequestContextMiddleware"
    assert "apps.core.middleware.DatabaseResilienceMiddleware" in middleware


def test_database_pool_is_bounded_in_production(production_settings) -> None:
    """استخر باید سقف صریح داشته باشد؛ وگرنه دوباره به «too many clients» می‌رسیم."""
    options = production_settings.DATABASES["default"]["OPTIONS"]
    pool = options.get("pool")
    assert isinstance(pool, dict), "استخر اتصال در تولید فعال نیست."
    assert pool["max_size"] >= 1
    assert pool["timeout"] > 0
    assert production_settings.DATABASES["default"]["CONN_MAX_AGE"] == 0


def test_capacity_formula_matches_postgres_budget(production_settings) -> None:
    """ظرفیت نظری (کارگر × استخر) باید زیر سقف رایج Postgres بماند."""
    pool_max = production_settings.DATABASES["default"]["OPTIONS"]["pool"]["max_size"]
    workers = 5  # WEB_CONCURRENCY پیش‌فرض در Dockerfile/systemd (فرمول ۲×هسته+۱)
    reserve = 10  # مهاجرت، پشتیبان، اتصال اپراتور
    assert workers * pool_max + reserve <= 100, (
        "ظرفیت اتصال از بودجهٔ Postgres پیشی می‌گیرد؛ فرمول در docs/runbooks/deploy.md را به‌روز کنید."
    )


def test_production_hardening_flags(production_settings) -> None:
    """دکمه‌های امنیتی تولید باید روشن باشند."""
    assert production_settings.SECURE_SSL_REDIRECT is True
    assert production_settings.SECURE_HSTS_SECONDS >= 31_536_000
    assert production_settings.SESSION_COOKIE_SECURE is True
    assert production_settings.CSRF_COOKIE_SECURE is True
    assert production_settings.DEBUG is False


def test_production_refuses_to_boot_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """بدون راز الزامی، فرآیند بالا نمی‌آید (fail-fast، نه شکست خاموش)."""
    env = {
        "PATH": "/usr/bin:/bin",
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DATABASE_PASSWORD": "x",
        # MAHDIEH_SECRET_KEY و MAHDIEH_ALLOWED_HOSTS عمداً غایب‌اند
    }
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings.production"],
        cwd=str(pathlib.Path(settings.BASE_DIR)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "MAHDIEH_SECRET_KEY" in (result.stderr + result.stdout)


def _boot_production(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """تنظیمات تولید را در فرآیندی جدا با محیطِ *دقیقاً معلوم* بارگذاری می‌کند.

    چرا فرآیند جدا؟ ``config.settings.production`` روی ``base`` سوار است و base
    یک بار (با محیط نخست) ساخته و کش می‌شود؛ پس در همان فرآیند آزمون، تغییر
    ``DATABASE_URL`` اثری ندارد. فرآیند تازه = محیط تمیز = آنچه روی سرور واقعی
    رخ می‌دهد.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ROOT / "src"),
        "MAHDIEH_SECRET_KEY": SECRET_KEY_FOR_TEST,
        "MAHDIEH_ALLOWED_HOSTS": "mahdieh.ir",
        **extra_env,
    }
    # اجرا در پوشه‌ای خالی: تنظیمات، فایل ``BASE_DIR/.env`` را می‌خواند و اگر
    # پوشهٔ آزمون جاری، ``src`` باشد همان فایل لوکال می‌تواند نتیجه را عوض کند.
    # تولید واقعی هم ``.env`` ندارد؛ چیزی که این‌جا آزموده می‌شود همان حالت سرور است.
    with tempfile.TemporaryDirectory() as scratch:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import config.settings.production as s; "
                "print(repr(s.DATABASES['default']['PASSWORD']))",
            ],
            cwd=scratch,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


def test_database_password_may_live_inside_database_url() -> None:
    """گذرواژهٔ پایگاه‌داده: «داخل DATABASE_URL» باید کافی باشد.

    داستان واقعی پشت این آزمون: در نخستین اجرای کانتینر تولیدی در CI، استقرار با
    ``KeyError: 'DATABASE_PASSWORD'`` مرد؛ چون تنظیمات فقط متغیر جدا را می‌خواند،
    حال آنکه هر اپراتوری به‌طور طبیعی گذرواژه را داخل ``DATABASE_URL`` می‌گذارد.
    قرارداد: هر کدام از دو راه کافی است.
    """
    result = _boot_production({"DATABASE_URL": "postgresql://mahdieh:s3cret@db:5432/mahdieh"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "'s3cret'"


def test_database_password_of_only_spaces_counts_as_absent() -> None:
    """``DATABASE_PASSWORD=   `` (فقط فاصله) هم «نبودن» است، نه یک گذرواژه.

    در آزمون واقعی دیدیم چنین خطی در فایل محیط، «مقدار» شمرده می‌شد و به‌جای
    پیام روشن، خطای احراز هویت پایگاه‌داده می‌داد.
    """
    result = _boot_production(
        {
            "DATABASE_URL": "postgresql://mahdieh@db:5432/mahdieh",
            "DATABASE_PASSWORD": "   ",
        }
    )
    assert result.returncode != 0


def test_database_password_absent_everywhere_is_fatal() -> None:
    """و «هیچ‌کجا نبودن» گذرواژه باز هم فرآیند را می‌بندد (fail-fast)."""
    result = _boot_production({"DATABASE_URL": "postgresql://mahdieh@db:5432/mahdieh"})
    assert result.returncode != 0
    assert "DATABASE_PASSWORD" in (result.stderr + result.stdout)


def test_database_password_variable_wins_over_url() -> None:
    """اگر هر دو باشند، متغیر جدا اولویت دارد (راز، بیرون از URL)."""
    result = _boot_production(
        {
            "DATABASE_URL": "postgresql://mahdieh:from-url@db:5432/mahdieh",
            "DATABASE_PASSWORD": "from-variable",
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "'from-variable'"


def test_short_secret_key_is_rejected(production_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """راز کوتاه هم پذیرفته نمی‌شود (سقف ۵۰ نویسه)."""
    monkeypatch.setenv("MAHDIEH_SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        importlib.reload(production_settings)


def test_asgi_entrypoint_is_wrapped_with_load_shedding() -> None:
    """نقطهٔ ورود ASGI باید سدِ ظرفیت را دور جنگو بپیچد."""
    from apps.core.shedding import ConcurrencyLimitMiddleware
    from config.asgi import application

    if settings.LOAD_SHEDDING_ENABLED:
        assert isinstance(application, ConcurrencyLimitMiddleware)
    else:  # pragma: no cover - حالت خاموشی عمدی
        assert application is not None


def test_health_endpoints_exist_and_are_cheap(client) -> None:
    """سه مسیر سلامت باید وجود داشته باشند و بدون احراز هویت پاسخ دهند."""
    for path in ("/health/live/", "/health/ready/", "/health/detailed/"):
        response = client.get(path)
        # detailed برای ناشناس بسته است (۴۰۳) و ready در نبود وابستگی ۵۰۳ می‌دهد؛
        # هیچ‌کدام «۴۰۴» نباید باشد، وگرنه مسیر یا پایشگر گم شده است.
        assert response.status_code in (200, 403, 503), (path, response.status_code)
