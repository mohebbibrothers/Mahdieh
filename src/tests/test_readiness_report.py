"""قرارداد برگهٔ آمادگی انتشار — «ابزاری که می‌تواند شکست را هم بگوید».

خطر این‌گونه ابزارها همیشه یکی است: روزی می‌رسد که همیشه «سبز» می‌گویند، چون
کسی آزمونشان نکرده که بتوانند قرمز شوند. پس این‌جا هم مسیر سبز آزموده می‌شود و
هم **توانایی قرمز شدن** — با حذف موقت یک فایل الزامی.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "readiness_report.py"


def _load_module() -> ModuleType:
    """بارگذاری اسکریپت به‌عنوان ماژول (بدون اجرای main).

    نکتهٔ فنی: ماژول باید **پیش از** اجرا در ``sys.modules`` ثبت شود؛ وگرنه
    دکوراتور ``dataclass`` با ``from __future__ import annotations`` ماژول را
    پیدا نمی‌کند و بارگذاری می‌شکند.
    """
    spec = importlib.util.spec_from_file_location("readiness_report_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_report_lists_every_required_artifact() -> None:
    """فهرست الزامات نباید خالی یا تُنُک باشد؛ نبودِ هر قلم باید دیده شود."""
    module = _load_module()
    paths = {path for path, _ in module.REQUIRED_ARTIFACTS}
    for expected in (
        "Dockerfile",
        "entrypoint.sh",
        "deploy/nginx/mahdieh.conf",
        "docs/runbooks/golive.md",
        "docs/runbooks/monitoring.md",
        "scripts/sms_golive.sh",
        "scripts/preflight_production.sh",
    ):
        assert expected in paths, f"«{expected}» در فهرست الزامات انتشار نیست."


def test_fast_report_is_written_and_has_a_verdict(tmp_path: Path) -> None:
    """اجرای سریع: برگه ساخته می‌شود، حکم دارد و کامیت را ثبت می‌کند."""
    module = _load_module()
    output = tmp_path / "readiness.md"
    code = module.main(["--fast", "--quiet", "--output", str(output)])

    text = output.read_text(encoding="utf-8")
    assert code in (0, 1)
    assert "# برگهٔ آمادگی انتشار" in text
    assert "حکم" in text
    assert "کامیت" in text
    assert "پس از انتشار" in text, "برگه باید کارهای پس از انتشار را هم فهرست کند."


def test_report_can_fail_when_an_artifact_is_missing(tmp_path: Path, monkeypatch) -> None:
    """توانایی قرمز شدن: اگر یک فایل الزامی غایب باشد، حکم «آماده» نمی‌ماند."""
    module = _load_module()
    module.REQUIRED_ARTIFACTS = (
        *module.REQUIRED_ARTIFACTS,
        ("docs/این-فایل-وجود-ندارد.md", "قلم آزمونی"),
    )
    output = tmp_path / "readiness.md"
    code = module.main(["--fast", "--quiet", "--output", str(output)])

    assert code == 1, "نبودِ یک قلم الزامی باید کد خروج ۱ بدهد."
    assert "آماده نیست" in output.read_text(encoding="utf-8")


def test_persian_digits_are_used_in_the_report(tmp_path: Path) -> None:
    """خروجی چشم‌آدم باید ارقام فارسی داشته باشد (ADR-0014)."""
    module = _load_module()
    output = tmp_path / "readiness.md"
    module.main(["--fast", "--quiet", "--output", str(output)])
    text = output.read_text(encoding="utf-8")
    assert "۲۴" in text or "۰" in text, "دست‌کم یک عدد فارسی در برگه باید باشد."
    assert "24 فایل" not in text


def test_capacity_sentinel_counts_only_in_header(tmp_path: Path, monkeypatch) -> None:
    """نشانهٔ «عدد ثبت شد» فقط از سرصفحهٔ برگه خوانده می‌شود.

    این آزمون از یک تلهٔ واقعی زاده شد: نسخهٔ نخستِ بررسی، کل متن را می‌گشت و
    متنِ «راهنمای پر کردن» که خودش نشانه را مثال می‌زد، بررسی را فریب داد و
    برگه بدون هیچ عددی «✅ ثبت شد» گفت. ابزاری که با متنِ راهنمای خودش فریب
    بخورد، بدتر از نبودنِ آن است.
    """
    module = _load_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    record = tmp_path / "docs" / "load" / "production-capacity-record.md"
    record.parent.mkdir(parents=True)

    def status_after_building() -> str:
        report = module.Report(commit="x", branch="main", dirty=False, generated_at="now")
        module.check_capacity_record(report)
        return report.results[-1].status

    # نشانه فقط در متنِ راهنما آمده ⇒ هنوز «اندازه‌گیری نشده»
    record.write_text(
        "# برگه\n\nراهنما: واژه را به `<!-- capacity-status: recorded -->` تغییر دهید\n",
        encoding="utf-8",
    )
    assert status_after_building() == "warn"

    # نشانه در سرصفحه ⇒ ثبت‌شده
    record.write_text("# برگه\n<!-- capacity-status: recorded -->\n\n…\n", encoding="utf-8")
    assert status_after_building() == "ok"

    # برگهٔ غایب هم «ناشناخته» است، نه «سالم»
    record.unlink()
    assert status_after_building() == "warn"
