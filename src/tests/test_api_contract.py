"""قرارداد API — آزمون‌هایی که «سند» را به «تعهد» تبدیل می‌کنند.

چرا وجود دارند؟ کار شرکت دو تیم است: بک‌اند (ما) و فرانت (تیم جدا). تیم فرانت
کلاینت تایپ‌دارش را از سند OpenAPI می‌سازد؛ پس هر نقطه‌ای که در سند نیامده باشد یا
شکلی بی‌سریال داشته باشد، عملاً **وجود ندارد** و کار فرانت می‌خوابد. این آزمون‌ها
همان شکاف را می‌بندند:

* سند بی‌هیچ خطا/هشدار تولید می‌شود (``--fail-on-warn``) و با استاندارد OpenAPI
  معتبر است (``--validate``).
* هر مسیر واقعی زیر ``/api/v1/`` در سند هست (پوشش کامل — نه سند ناقص).
* هر عملیات، پاسخ موفق **با شکل** و ``summary``/``tags`` فارسی دارد.
* ``operationId``ها یکتا هستند (مولد کلاینت فرانت روی تکراری‌ها می‌شکند).
* فایل متعهدشدهٔ ``docs/api/openapi.yaml`` با تولید تازه **یکی** است؛ سند کهنه
  هرگز به دست فرانت نمی‌رسد.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import re

from django.core.management import call_command
from django.urls import get_resolver
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = REPO_ROOT / "docs" / "api" / "openapi.yaml"
API_PREFIX = "api/v1/"
SUCCESS_STATUSES = ("200", "201", "202", "204")
EMPTY_SUCCESS = "204"
JSON_MEDIA_TYPES = ("application/json", "application/problem+json")
# مسیرهای پارامتری جنگو (``<uuid:block_id>``) در سند با آکولاد می‌آیند.
PARAM_PATTERN = re.compile(r"<(?:[a-z]+:)?([a-zA-Z_]+)>")


def _api_routes() -> set[str]:
    """همهٔ مسیرهای واقعی زیر ``/api/v1/`` — از URLconf، نه از سند."""
    routes: list[str] = []

    def walk(resolver: object, prefix: str = "") -> None:
        for pattern in resolver.url_patterns:  # type: ignore[attr-defined]
            if hasattr(pattern, "url_patterns"):
                walk(pattern, prefix + str(pattern.pattern))
            else:
                routes.append(prefix + str(pattern.pattern))

    walk(get_resolver())
    return {
        "/" + PARAM_PATTERN.sub(r"{\1}", route.rstrip("/"))
        for route in routes
        if route.startswith(API_PREFIX)
    }


def _generate(target: Path) -> dict[str, object]:
    """تولید سند در همین لحظه — با اعتبارسنجی و بی‌هیچ هشدار."""
    call_command("spectacular", file=str(target), validate=True, fail_on_warn=True)
    return yaml.safe_load(target.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """سند تولیدشده در همان لحظهٔ آزمون (نه فایل قدیمی)."""
    target = tmp_path_factory.mktemp("openapi") / "schema.yaml"
    return _generate(target)


def _operations(schema: dict[str, object]) -> list[tuple[str, str, dict[str, object]]]:
    """همهٔ عملیات سند به‌صورت (مسیر، فعل، بدنه)."""
    paths: dict[str, dict[str, dict[str, object]]] = schema["paths"]  # type: ignore[assignment]
    verbs = {"get", "post", "put", "patch", "delete"}
    return [
        (path, verb, body)
        for path, methods in paths.items()
        for verb, body in methods.items()
        if verb in verbs
    ]


def test_schema_generates_clean_and_valid(tmp_path: Path) -> None:
    """تولید سند بی‌خطا/هشدار و معتبر در برابر استاندارد OpenAPI."""
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", file=str(target), validate=True, fail_on_warn=True)
    assert target.read_text(encoding="utf-8").startswith("openapi: 3.0")


def test_every_api_route_is_documented(schema: dict[str, object]) -> None:
    """هر مسیر واقعی API در سند هست — سند ناقص، فرانت را زمین می‌زند."""
    documented = {path.rstrip("/") for path in schema["paths"]}  # type: ignore[union-attr]
    missing = sorted(route for route in _api_routes() if route not in documented)
    assert missing == [], f"این مسیرها در سند OpenAPI نیستند: {missing}"


def test_every_operation_has_success_schema_and_fa_copy(schema: dict[str, object]) -> None:
    """هر عملیات: پاسخ موفق با شکل مشخص + خلاصهٔ فارسی + برچسب."""
    problems: list[str] = []
    for path, verb, body in _operations(schema):
        responses: dict[str, object] = body.get("responses", {})  # type: ignore[assignment]
        success = next((key for key in SUCCESS_STATUSES if key in responses), None)
        if success is None:
            problems.append(f"{verb.upper()} {path}: پاسخ موفق ۲۰۰/۲۰۱/۲۰۲ ندارد")
            continue
        declared = responses[success]
        content = declared.get("content", {}) if isinstance(declared, dict) else {}
        if success == EMPTY_SUCCESS:
            # ۲۰۴ یعنی «بدنه‌ای نیست»؛ سند هم نباید شکلی برایش وعده بدهد.
            if content:
                problems.append(f"{verb.upper()} {path}: پاسخ ۲۰۴ نباید بدنه داشته باشد")
        elif not content:
            problems.append(f"{verb.upper()} {path}: پاسخ {success} هیچ نوع محتوایی ندارد")
        elif any(media in content for media in JSON_MEDIA_TYPES):
            schema_ = next(
                content[media].get("schema") for media in JSON_MEDIA_TYPES if media in content
            )
            if not schema_:
                problems.append(f"{verb.upper()} {path}: پاسخ {success} شکل (schema) ندارد")
        else:
            # دانلود فایل — نوع محتوای اعلام‌شده کافی است (مثل xlsx).
            for media, media_body in content.items():
                if not media_body.get("schema"):
                    problems.append(f"{verb.upper()} {path}: {media} بدون شکل (schema) است")
        if not body.get("summary"):
            problems.append(f"{verb.upper()} {path}: خلاصه (summary) ندارد")
        if not body.get("tags"):
            problems.append(f"{verb.upper()} {path}: برچسب (tags) ندارد")
    assert problems == [], "نقص‌های سند:\n" + "\n".join(problems)


def test_operation_ids_are_unique(schema: dict[str, object]) -> None:
    """شناسهٔ یکتای هر عملیات — پیش‌نیاز تولید کلاینت."""
    ids = [str(body.get("operationId")) for _, _, body in _operations(schema)]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert duplicates == [], f"operationId تکراری: {duplicates}"


def test_block_period_is_structured_object(schema: dict[str, object]) -> None:
    """بازهٔ بلوک به‌شکل شیء ``{start, end}`` مستند شده است، نه رشتهٔ JSON.

    پیش از این، خروجی ``Block.period`` رشتهٔ JSON فرارکرده بود
    (``'{"bounds": "[)", …}'``) و کلاینت فرانت باید دو بار parse می‌کرد.
    """
    block = schema["components"]["schemas"]["Block"]  # type: ignore[index]
    period = block["properties"]["period"]
    assert period.get("type") == "object", period
    assert {"start", "end"} <= set(period.get("properties", {})), period


def test_committed_schema_matches_fresh_generation(tmp_path: Path) -> None:
    """فایل متعهدشدهٔ ``docs/api/openapi.yaml`` با تولید تازه یکی است.

    اگر کسی نقطهٔ تازه‌ای اضافه کند و سند را بازتولید نکند، این آزمون قرمز
    می‌شود — چون فرانت باید همیشه سند به‌روز را از مخزن بردارد.
    """
    assert SCHEMA_FILE.exists(), "docs/api/openapi.yaml در مخزن نیست"
    fresh = _generate(tmp_path / "openapi.yaml")
    committed = yaml.safe_load(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert committed == fresh, "سند متعهدشده با تولید تازه فرق دارد؛ اجرا کنید: make schema"


def test_schema_metadata_is_descriptive(schema: dict[str, object]) -> None:
    """سند خودش را معرفی می‌کند: عنوان، نسخه و توضیح قرارداد."""
    info: dict[str, str] = schema["info"]  # type: ignore[assignment]
    assert info["title"].strip()
    assert info["version"].strip()
    description = info.get("description", "")
    assert "problem+json" in description  # قرارداد خطا در سند گفته شده است
    assert "Idempotency-Key" in description  # قرارداد هم‌توانی هم


def test_schema_command_prints_nothing_on_stderr() -> None:
    """خروجی فرمان سند در حالت تمیز، هیچ لاگ تشخیصی روی stderr ندارد."""
    out, err = StringIO(), StringIO()
    call_command("spectacular", file="/dev/null", stdout=out, stderr=err)
    assert err.getvalue().strip() == ""
