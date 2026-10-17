"""سرویس گزارش — از بازهٔ درخواستی تا فایل مهر‌وموم‌شدهٔ اکسل.

هر خروجی سه چیز دارد: فایل، هش (حاکمیت داده) و ثبت حسابرسی. فلسفهٔ این‌جا
روشن است: «هر که گزارش می‌گیرد، ثبت می‌شود؛ و هر سند، اثر انگشت دارد.»
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.errors import ValidationError as DomainValidationError
from apps.identity.models import User
from apps.reporting import excel
from apps.reporting.archive import XLSX_EXTENSION, sha256_hex, workbook_bytes
from apps.reporting.dataset import ReportData, build_dataset
from apps.reporting.ranges import ReportRange, parse_range

EXPORT_REASON = "دریافت خروجی اکسل گزارش رزروها"
EXPORT_REASON_FULL_PHONES = "دریافت خروجی اکسل گزارش رزروها با شمارهٔ کامل (مدیر، تأیید تازه)"


@dataclass(frozen=True, slots=True)
class ReportFile:
    """فایل گزارش آمادهٔ دانلود — با همهٔ متادادهٔ حاکمیتی."""

    filename: str
    content: bytes
    sha256: str
    payload_digest: str
    rows: dict[str, int]
    totals: dict[str, Any]
    range_label: str
    generated_at: datetime


def build_report(
    *,
    from_raw: str | None = None,
    to_raw: str | None = None,
    unit_codes: tuple[str, ...] = (),
    statuses: tuple[str, ...] = (),
    actor: User | None = None,
    generated_at: datetime | None = None,
    request: HttpRequest | None = None,
    reveal_phones: bool = False,
) -> ReportFile:
    """ساخت گزارش کامل اکسل + ثبت کنش در زنجیرهٔ حسابرسی."""
    report_range = parse_range(from_raw=from_raw, to_raw=to_raw)
    return build_report_for_range(
        report_range=report_range,
        unit_codes=unit_codes,
        statuses=statuses,
        reveal_phones=reveal_phones,
        actor=actor,
        generated_at=generated_at,
        request=request,
    )


def build_report_for_range(
    *,
    report_range: ReportRange,
    unit_codes: tuple[str, ...] = (),
    statuses: tuple[str, ...] = (),
    actor: User | None = None,
    generated_at: datetime | None = None,
    request: HttpRequest | None = None,
    reveal_phones: bool = False,
) -> ReportFile:
    """ساخت گزارش برای یک بازهٔ اعتبارسنجی‌شده."""
    if unit_codes:
        _ensure_units_exist(unit_codes)
    moment = generated_at or timezone.now()
    data: ReportData = build_dataset(
        report_range=report_range,
        generated_at=moment,
        unit_codes=unit_codes,
        statuses=statuses,
        reveal_phones=reveal_phones,
    )
    content = workbook_bytes(excel.build_workbook(data), modified_at=moment)
    report = ReportFile(
        filename=report_filename(report_range, moment),
        content=content,
        sha256=sha256_hex(content),
        payload_digest=data.payload_digest(),
        rows=data.row_counts,
        totals=data.totals,
        range_label=report_range.label,
        generated_at=moment,
    )
    _audit(
        report,
        report_range=report_range,
        actor=actor,
        request=request,
        phones_revealed=reveal_phones,
    )
    return report


def report_filename(report_range: ReportRange, generated_at: datetime) -> str:
    """نام فایل لاتین و پایدار: ``mahdieh-report-<بازهٔ شمسی>.xlsx``."""
    del generated_at  # نام فایل باید فقط تابع بازه باشد تا بازتولیدپذیر بماند
    return f"mahdieh-report-{report_range.slug}.{XLSX_EXTENSION}"


def summary_only(*, report_range: ReportRange, unit_codes: tuple[str, ...] = ()) -> dict[str, Any]:
    """خلاصهٔ عددی گزارش — برای پیش‌نمایش در رابط کاربری، بدون ساخت فایل."""
    data = build_dataset(report_range=report_range, unit_codes=unit_codes)
    return {
        "range": {
            "start": report_range.start_day.isoformat(),
            "end": report_range.end_day.isoformat(),
            "label": report_range.label,
            "days": report_range.days,
        },
        "rows": data.row_counts,
        "totals": data.totals,
        "payload_digest": data.payload_digest(),
    }


def _ensure_units_exist(unit_codes: tuple[str, ...]) -> None:
    """اعتبارسنجی کدهای واحد — خطای روشن به‌جای گزارش خالی."""
    from apps.inventory.models import Unit

    known = set(Unit.objects.filter(code__in=unit_codes).values_list("code", flat=True))
    unknown = sorted(set(unit_codes) - known)
    if unknown:
        raise DomainValidationError(
            _("کد واحد ناشناخته: %(codes)s") % {"codes": "، ".join(unknown)},
            code="unknown_unit",
        )


def _audit(
    report: ReportFile,
    *,
    report_range: ReportRange,
    actor: User | None,
    request: HttpRequest | None,
    phones_revealed: bool = False,
) -> None:
    """ثبت خروجی در زنجیرهٔ حسابرسی — «هر خروجی، ردپا دارد»."""
    audit_record(
        AuditEntry(
            action=AuditAction.EXPORT_REQUESTED,
            actor_id=getattr(actor, "pk", None),
            actor_label=getattr(actor, "display_name", "") or "سیستم",
            target_type="report",
            target_id="reservations",
            reason=EXPORT_REASON_FULL_PHONES if phones_revealed else EXPORT_REASON,
            metadata={
                "phones": "full" if phones_revealed else "masked",
                "range": [report_range.start_day.isoformat(), report_range.end_day.isoformat()],
                "rows": report.rows,
                "file_sha256": report.sha256,
                "payload_digest": report.payload_digest,
            },
            request=request,
        )
    )
