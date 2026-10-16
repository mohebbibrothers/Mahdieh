"""گیت‌های انضباط معماری — قواعدی که با بازبینی انسانی شکسته می‌شوند.

اصل ۴ سند معماری: «مرزها را کد بزن، نه توصیه». این تست‌ها AST می‌خوانند و
هر تخلف را در CI به شکست تبدیل می‌کنند.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

SRC = Path(__file__).resolve().parents[1]
APPS_DIR = SRC / "apps"

# ماژول‌هایی که نوشتن مستقیم در آن‌ها مجاز نیست
VIEW_MODULES = list(APPS_DIR.glob("*/views*.py"))
WRITE_METHODS = frozenset(
    {"create", "update", "delete", "bulk_create", "update_or_create", "get_or_create"}
)

# نشانه‌های «گیرندهٔ دیتابیسی»: مدیر مدل یا کوئری‌ست. قاعده فقط روی این‌ها
# سخت‌گیر است تا ``context.update({...})`` روی دیکشنری گزارش نشود — یعنی
# دروازه دقیق باشد، نه پرصدا.
QUERYSET_HINTS = frozenset(
    {
        "objects",
        "all_objects",
        "filter",
        "exclude",
        "all",
        "get",
        "select_for_update",
        "select_related",
        "prefetch_related",
    }
)


def _receiver_chain(node: ast.AST) -> set[str]:
    """زنجیرهٔ نام‌های گیرندهٔ یک فراخوانی: ``A.objects.filter().update`` → {A, objects, filter}."""
    names: set[str] = set()
    current: ast.AST = node
    while True:
        if isinstance(current, ast.Call):  # filter(...).update(...) — پوستهٔ فراخوانی
            names.add("call")
            current = current.func
            continue
        if isinstance(current, ast.Attribute):
            names.add(current.attr)
            current = current.value
            continue
        break
    if isinstance(current, ast.Name):
        names.add(current.id)
    return names


def _python_files(directory: Path) -> list[Path]:
    """همهٔ فایل‌های پایتون، بدون مهاجرت و کش."""
    return [
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts and "migrations" not in path.parts
    ]


@pytest.mark.architecture
def test_every_module_has_docstring() -> None:
    """هر ماژول تولیدی باید مستندسازی داشته باشد."""
    missing: list[str] = []
    for path in _python_files(APPS_DIR):
        if path.name == "__init__.py" and not path.read_text(encoding="utf-8").strip():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if ast.get_docstring(tree) is None:
            missing.append(str(path.relative_to(SRC)))
    assert not missing, f"ماژول‌های بدون docstring: {missing}"


@pytest.mark.architecture
def test_public_classes_and_functions_have_docstrings() -> None:
    """هر کلاس/تابع عمومی باید docstring داشته باشد."""
    missing: list[str] = []
    for path in _python_files(APPS_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") or node.name == "Meta":
                    continue  # کلاس Meta قرارداد جنگو است، نه API عمومی
                if ast.get_docstring(node):
                    continue
                missing.append(f"{path.relative_to(SRC)}::{node.name}")
    assert not missing, f"موارد بدون docstring: {missing}"


@pytest.mark.architecture
def test_views_never_write_directly_to_database() -> None:
    """نمای HTTP نباید مستقیم روی مدل/کوئری‌ست بنویسد (قاعدهٔ لایه‌بندی).

    نوشتن باید از طریق ``services`` انجام شود؛ این قاعده در پروژهٔ پیشین هم
    وجود داشت و اینجا **اجرا** می‌شود.
    """
    violations: list[str] = []
    for path in VIEW_MODULES:
        violations.extend(
            f"{path.relative_to(SRC)}::{snippet}"
            for snippet in _direct_write_violations(path.read_text(encoding="utf-8"))
        )
    assert not violations, f"نوشتن مستقیم دیتابیس در نماها: {violations}"


def _direct_write_violations(source: str) -> list[str]:
    """یافتن نوشتن مستقیم دیتابیس در یک فایل نمای HTTP.

    قاعده: فراخوانی یکی از ``WRITE_METHODS`` روی گیرنده‌ای که نشانهٔ کوئری‌ست یا
    مدیر مدل دارد (``Model.objects``، ``queryset.filter(...)``) تخلف است.
    ``dict.update`` و ``set.update`` تخلف نیستند — تفکیک عمدی و آزموده‌شده.
    """
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in WRITE_METHODS:
            continue
        if not (QUERYSET_HINTS & _receiver_chain(node.func.value)):
            continue
        found.append(ast.unparse(node)[:60])
    return found


@pytest.mark.architecture
def test_direct_write_rule_is_precise_not_noisy() -> None:
    """خودِ قاعده آزموده می‌شود: تخلف واقعی را می‌گیرد و مثبت کاذب نمی‌سازد."""
    offender = (
        "def view(request):\n    return Reservation.objects.filter(pk=1).update(status='x')\n"
    )
    assert _direct_write_violations(offender), "قاعده باید نوشتن کوئری‌ستی را بگیرد"
    innocent = (
        "def view(request):\n    context = {}\n    context.update({'a': 1})\n    return context\n"
    )
    assert not _direct_write_violations(innocent), "dict.update نباید تخلف شمرده شود"


@pytest.mark.architecture
def test_no_todo_without_issue_reference() -> None:
    """هر TODO/FIXME باید شمارهٔ issue داشته باشد — بدهی پنهان ممنوع."""
    pattern = re.compile(r"(TODO|FIXME)(?!.*#\d+|.*https?://)")
    offenders: list[str] = []
    # فقط کد تولیدی بررسی می‌شود؛ خودِ همین تست، الگو را در متن دارد.
    for path in _python_files(APPS_DIR):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{number}")
    assert not offenders, f"TODO/FIXME بدون ارجاع به issue: {offenders}"


@pytest.mark.architecture
def test_no_print_statements_in_source() -> None:
    """``print`` در کد تولیدی ممنوع است — لاگ ساخت‌یافته جای آن است."""
    offenders: list[str] = []
    for path in _python_files(SRC):
        if path.parts[-2] == "tests" or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, f"print در کد تولیدی: {offenders}"


@pytest.mark.architecture
def test_domain_modules_do_not_import_http_layer() -> None:
    """مدل و سرویس نباید به لایهٔ HTTP (DRF) وابسته باشند."""
    forbidden = ("rest_framework",)
    offenders: list[str] = []
    for name in ("models.py", "services.py", "selectors.py"):
        for path in APPS_DIR.glob(f"*/{name}"):
            text = path.read_text(encoding="utf-8")
            for module in forbidden:
                if f"import {module}" in text or f"from {module}" in text:
                    offenders.append(f"{path.relative_to(SRC)} → {module}")
    assert not offenders, f"وابستگی معکوس لایه‌ها: {offenders}"
