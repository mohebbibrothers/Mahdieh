"""بسته‌بندی بازتولیدپذیر فایل اکسل.

چرا این لایه لازم است؟ یک فایل ``.xlsx`` در واقع یک آرشیو ZIP است و هر ابزار
سازنده، مُهر زمانی «الان» را داخل آرشیو می‌نویسد. نتیجه: دو اجرای کاملاً یکسان،
دو فایل با هش متفاوت — و «سند قابل ارجاع» بی‌معنا می‌شود.

راه‌حل: بایت‌های تولیدشده را از نو بسته‌بندی می‌کنیم با
    • ترتیب الفبایی نام اعضا،
    • مُهر زمانی ثابت ``1980-01-01`` (نقطهٔ صفر ZIP)،
    • همان الگوریتم فشرده‌سازی.
این‌گونه هش فایل، تابع «محتوا» است و نه «لحظهٔ تولید».
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import io
import re
import zipfile

from openpyxl import Workbook

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_EXTENSION = "xlsx"


MODIFIED_TAG_PATTERN = re.compile(rb"<dcterms:modified[^>]*>[^<]*</dcterms:modified>")
CORE_PART = "docProps/core.xml"


def workbook_bytes(workbook: Workbook, *, modified_at: datetime | None = None) -> bytes:
    """ساخت بایت‌های نهایی اکسل: ذخیره + مهر زمانی ثابت + بسته‌بندی قطعی.

    نکتهٔ مهم: ``openpyxl`` هنگام ذخیره، ``dcterms:modified`` را با «همین حالا»
    بازنویسی می‌کند — حتی اگر پیش از ذخیره مقداردهی شده باشد. پس مُهر زمانی را
    پس از ذخیره در خودِ عضو ``docProps/core.xml`` قفل می‌کنیم تا دو خروجی یکسان،
    فایل یکسان بدهند.
    """
    buffer = io.BytesIO()
    workbook.save(buffer)
    replacements: dict[str, bytes] = {}
    if modified_at is not None:
        replacements[CORE_PART] = _pinned_core(raw=buffer.getvalue(), moment=modified_at)
    return _deterministic_zip(buffer.getvalue(), replacements=replacements)


def _pinned_core(*, raw: bytes, moment: datetime) -> bytes:
    """جای‌گذاری مُهر زمانی قطعی در ``docProps/core.xml``."""
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        content = source.read(CORE_PART)
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    replacement = (
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
    ).encode()
    return MODIFIED_TAG_PATTERN.sub(replacement, content, count=1)


def sha256_hex(content: bytes) -> str:
    """اثر انگشت SHA-256 محتوای فایل."""
    return hashlib.sha256(content).hexdigest()


def _deterministic_zip(raw: bytes, *, replacements: dict[str, bytes] | None = None) -> bytes:
    """بازنویسی آرشیو با ترتیب و مُهر زمانی ثابت (+ جای‌گذاری عضوهای قفل‌شده)."""
    pinned = replacements or {}
    target_buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(target_buffer, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(filename=name, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            target.writestr(info, pinned.get(name, source.read(name)))
    return target_buffer.getvalue()
