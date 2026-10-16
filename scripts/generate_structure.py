#!/usr/bin/env python
"""بازتولید ``STRUCTURE.md`` از خود مخزن و بررسی drift (گیت CI).

چرا؟ در پروژهٔ پیشین، سند ساختار دستی نگه‌داری می‌شد و کهنه شد. سندی که
«از خود کد تولید می‌شود» نمی‌تواند بی‌صدا کهنه شود — یا درست است یا CI قرمز.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
APPS = SRC / "apps"
TARGET = ROOT / "STRUCTURE.md"

LAYER_ROLE = {
    "models.py": "مدل‌های داده",
    "choices.py": "شمارشی‌های دامنه",
    "services.py": "منطق کسب‌وکار و نوشتن",
    "selectors.py": "خواندن داده (بدون side-effect)",
    "serializers.py": "اعتبارسنجی ورودی/خروجی",
    "views.py": "لایهٔ HTTP",
    "urls.py": "مسیرها",
    "permissions.py": "کنترل دسترسی",
    "throttles.py": "محدودسازی نرخ",
    "tasks.py": "کار پس‌زمینه",
    "events.py": "رخدادهای دامنه",
    "admin.py": "پنل مدیریت",
    "apps.py": "پیکربندی اپ",
}


def count_migrations(app: Path) -> int:
    """تعداد مهاجرت‌های یک اپ."""
    directory = app / "migrations"
    if not directory.exists():
        return 0
    return len(list(directory.glob("[0-9]*.py")))


def count_models(app: Path) -> int:
    """شمارش کلاس‌های مدل غیرمجرد در یک اپ."""
    path = app / "models.py"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    classes = re.findall(r"^class (\w+)\(([^)]*)\):", text, flags=re.MULTILINE)
    return len(
        [
            name
            for name, bases in classes
            if "abstract = True" not in text.split(f"class {name}")[1][:400]
            and "Migration" not in bases
        ]
    )


def count_tests(app: Path) -> int:
    """تعداد فایل‌های تست مربوط به اپ."""
    tests_dir = SRC / "tests"
    return len(list(tests_dir.glob(f"test_{app.name}_*.py"))) if tests_dir.exists() else 0


def render() -> str:
    """ساخت متن سند ساختار."""
    apps = sorted(
        [d for d in APPS.iterdir() if d.is_dir() and (d / "apps.py").exists()], key=lambda p: p.name
    )
    lines: list[str] = [
        "# ساختار پروژه",
        "",
        "> این فایل **تولیدشده** است؛ دستی ویرایش نکنید.",
        "> بازتولید: `uv run python scripts/generate_structure.py`",
        "> بررسی drift در CI: `uv run python scripts/generate_structure.py --check`",
        "",
        "## اپلیکیشن‌ها",
        "",
        "| اپ | مسئولیت | مدل | مهاجرت | فایل‌های تست |",
        "|---|---|---:|---:|---:|",
    ]
    module_desc = {
        "core": "هستهٔ مشترک: BaseModel، RFC 9457، Outbox، هم‌توانی، تقویم جلالی، سلامت",
        "identity": "هویت تلفن‌محور، پروفایل، نقش‌ها",
        "audit": "حسابرسی زنجیره‌ای و عدم‌انکار",
        "inventory": "واحدها، طبقات، امکانات، بلوک‌ها و شبکهٔ ساعتی نیم‌روز",
        "reservation": "چرخهٔ عمر رزرو، سیاست‌ها، تخصیص، گواهی و تقویم موجودی",
        "waitlist": "صف انتظار، پیشنهاد آبشاری و پذیرش یک‌کلیکی",
        "notifications": "پیامک و قالب‌ها با ارائه‌دهندهٔ ماک (تا اتصال پنل واقعی)",
        "analytics": "تحلیل اشغال، پیش‌بینی، رضایت زائر و داشبورد پذیرش",
        "reporting": "گزارش اکسل چندبرگی، راست‌به‌چپ و ممیزی‌شده",
        "web": "رابط SSR + HTMX: صفحات زائر، تابلوی پذیرش و صفحهٔ تأیید گواهی",
    }
    for app in apps:
        lines.append(
            f"| `{app.name}` | {module_desc.get(app.name, '—')} | {count_models(app)} | "
            f"{count_migrations(app)} | {count_tests(app)} |"
        )
    lines += ["", "## قرارداد لایه‌ها", "", "| فایل | مسئولیت |", "|---|---|"]
    for filename, role in LAYER_ROLE.items():
        lines.append(f"| `{filename}` | {role} |")
    lines += [
        "",
        "قواعدی که با تست (`src/tests/test_architecture_discipline.py`) اجرا می‌شوند:",
        "",
        "- هر ماژول تولیدی باید docstring داشته باشد.",
        "- هر کلاس/تابع عمومی باید docstring داشته باشد.",
        "- `views.py` نباید مستقیماً روی مدل/کوئری‌ست بنویسد (متناقض با لایهٔ سرویس).",
        "- `models.py`/`services.py` نباید به لایهٔ HTTP (DRF) وابسته باشند.",
        "- `TODO`/`FIXME` باید شمارهٔ issue داشته باشد.",
        "",
        "## درخت اپلیکیشن‌ها",
        "",
        "```text",
    ]
    for app in apps:
        lines.append(f"{app.name}/")
        for path in sorted(app.rglob("*.py")):
            if "migrations" in path.parts or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(app)
            role = LAYER_ROLE.get(path.name)
            suffix = f"  # {role}" if role else ""
            lines.append(f"├── {rel}{suffix}")
        lines.append("│")
    lines[-1] = "```" if lines[-1] == "│" else lines[-1]
    if not lines[-1].startswith("```"):
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """اجرای تولید یا بررسی drift."""
    content = render()
    if "--check" in sys.argv:
        if not TARGET.exists():
            print("❌ STRUCTURE.md وجود ندارد.")
            return 1
        if TARGET.read_text(encoding="utf-8") != content:
            print("❌ STRUCTURE.md با درخت واقعی مخزن هم‌خوان نیست. اجرا کنید:")
            print("   uv run python scripts/generate_structure.py")
            return 1
        print("✅ STRUCTURE.md هم‌خوان با مخزن است.")
        return 0

    TARGET.write_text(content, encoding="utf-8")
    print(f"✅ STRUCTURE.md بازتولید شد ({len(content.splitlines())} خط).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
