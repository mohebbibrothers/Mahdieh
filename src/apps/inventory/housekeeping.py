"""نوبت نظافت و آماده‌سازی واحد — چرخهٔ «تخلیه ⇒ نظافت ⇒ آمادهٔ تحویل» میز پذیرش.

این ماژول عمداً از رزرو جداست: موجودی را نمی‌بندد (سیاست فاصلهٔ نظافت صفر) و فقط وضعیت
عملیاتی واحد را نگه می‌دارد. هر آغاز و پایان در زنجیرهٔ حسابرسی ثبت می‌شود تا «چه کسی،
کی، کدام واحد را تحویل نظافت داد و کی آماده شد» همیشه پاسخ داشته باشد.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.errors import ConflictError, ValidationError as DomainValidationError
from apps.identity.choices import UserRole
from apps.identity.models import User
from apps.inventory.models import HousekeepingTask, Unit

MIN_MINUTES: Final[int] = 10
MAX_MINUTES: Final[int] = 240
DEFAULT_MINUTES: Final[int] = 45
#: گزینه‌های آمادهٔ مدت نظافت در پیشخوان (دقیقه) — هر عدد دیگر میان کمینه و بیشینه هم پذیرفته است.
PRESET_MINUTES: Final[tuple[int, ...]] = (30, 45, 60, 90)
ASSIGNEE_MAX: Final[int] = 80
SUGGESTION_LIMIT: Final[int] = 8


@dataclass(frozen=True, slots=True)
class Progress:
    """پیشرفت زمانی یک نوبت باز — برای نوار پیشرفت کارت واحد."""

    percent: int
    overdue: bool
    remaining_minutes: int


def open_task(unit: Unit) -> HousekeepingTask | None:
    """نوبت باز یک واحد (یا ``None``)."""
    return (
        HousekeepingTask.objects.filter(
            unit=unit, completed_at__isnull=True, deleted_at__isnull=True
        )
        .order_by("-started_at")
        .first()
    )


def open_tasks_by_unit() -> dict[str, HousekeepingTask]:
    """همهٔ نوبت‌های باز، کلید با کد واحد (یک پرس‌وجو برای کل تابلو)."""
    rows = (
        HousekeepingTask.objects.filter(completed_at__isnull=True, deleted_at__isnull=True)
        .select_related("unit", "started_by")
        .order_by("started_at")
    )
    return {row.unit.code: row for row in rows}


def recent_tasks(*, since: datetime) -> list[HousekeepingTask]:
    """نوبت‌هایی که از ``since`` آغاز یا تمام شده‌اند — برای رخدادنگار شیفت."""
    rows = HousekeepingTask.objects.filter(deleted_at__isnull=True).select_related(
        "unit", "started_by", "completed_by"
    )
    return list(
        rows.filter(started_at__gte=since) | rows.filter(completed_at__gte=since),
    )


def progress_of(task: HousekeepingTask, *, now: datetime | None = None) -> Progress:
    """درصد زمانی سپری‌شده (سقف ۹۹ تا تأیید پایان) و دقیقه‌های باقی‌مانده."""
    moment = now or timezone.now()
    total = max(1.0, (task.due_at - task.started_at).total_seconds())
    elapsed = max(0.0, (moment - task.started_at).total_seconds())
    remaining = int((task.due_at - moment).total_seconds() // 60)
    return Progress(
        percent=min(99, int(elapsed * 100 // total)),
        overdue=moment > task.due_at,
        remaining_minutes=max(0, remaining),
    )


def assignee_suggestions() -> list[str]:
    """پیشنهادهای نام خادم: نام‌های اخیر نوبت‌ها + نام کارکنان فعال (بی‌تکرار، به‌ترتیب تازگی)."""
    names: list[str] = []
    recent = (
        HousekeepingTask.objects.filter(deleted_at__isnull=True)
        .order_by("-started_at")
        .values_list("assignee", flat=True)[:40]
    )
    for name in recent:
        if name and name not in names:
            names.append(name)
        if len(names) >= SUGGESTION_LIMIT:
            return names
    staff = User.objects.filter(
        role__in=[role.value for role in UserRole.staff_roles()],
        is_active=True,
        deleted_at__isnull=True,
    ).order_by("first_name", "last_name")
    for user in staff[:20]:
        label = user.display_name.strip()
        if label and label not in names:
            names.append(label)
        if len(names) >= SUGGESTION_LIMIT:
            break
    return names


def _clean_assignee(raw: str) -> str:
    name = " ".join((raw or "").split())
    if len(name) < 2:  # noqa: PLR2004 - کوتاه‌ترین نام معنادار
        raise DomainValidationError(
            "نام خادم مسئول نظافت را بنویسید.",
            code="assignee_required",
            errors={"assignee": ["نام خادم مسئول نظافت را بنویسید."]},
        )
    return name[:ASSIGNEE_MAX]


def _clean_minutes(minutes: int | None) -> int:
    value = DEFAULT_MINUTES if minutes is None else int(minutes)
    if not MIN_MINUTES <= value <= MAX_MINUTES:
        raise DomainValidationError(
            f"مدت نظافت باید میان {MIN_MINUTES} تا {MAX_MINUTES} دقیقه باشد.",
            code="invalid_minutes",
            errors={"minutes": [f"میان {MIN_MINUTES} تا {MAX_MINUTES} دقیقه."]},
        )
    return value


def start(
    *,
    unit: Unit,
    assignee: str,
    minutes: int | None,
    by: User,
    reservation_code: str = "",
    note: str = "",
    request: HttpRequest | None = None,
) -> HousekeepingTask:
    """آغاز نوبت نظافت؛ نوبت باز دیگر روی همان واحد ⇒ ۴۰۹."""
    name = _clean_assignee(assignee)
    span = _clean_minutes(minutes)
    now = timezone.now()
    with transaction.atomic():
        # قفل ردیف واحد: دو خادم هم‌زمان دو نوبت باز برای یک واحد نمی‌سازند.
        Unit.objects.select_for_update().filter(pk=unit.pk).first()
        if open_task(unit) is not None:
            raise ConflictError(
                f"واحد {unit.code} هم‌اکنون در نوبت نظافت است.", code="housekeeping_open"
            )
        task = HousekeepingTask.objects.create(
            unit=unit,
            reservation_code=reservation_code[:24],
            assignee=name,
            note=" ".join((note or "").split())[:200],
            started_at=now,
            due_at=now + timedelta(minutes=span),
            started_by=by,
        )
        audit_record(
            AuditEntry(
                action=AuditAction.HOUSEKEEPING_STARTED,
                actor_id=by.pk,
                actor_label=by.display_name,
                target_type="unit",
                target_id=unit.code,
                reason=f"نظافت {unit.code} به {name} ({span} دقیقه)",
                metadata={"minutes": span, "reservation": reservation_code, "task": str(task.pk)},
                request=request,
            )
        )
    return task


def complete(*, unit: Unit, by: User, request: HttpRequest | None = None) -> HousekeepingTask:
    """تأیید پایان نظافت ⇒ واحد آمادهٔ تحویل؛ نبود نوبت باز ⇒ ۴۰۹."""
    with transaction.atomic():
        Unit.objects.select_for_update().filter(pk=unit.pk).first()
        task = open_task(unit)
        if task is None:
            raise ConflictError(
                f"واحد {unit.code} نوبت نظافت بازی ندارد.", code="housekeeping_not_open"
            )
        task.completed_at = timezone.now()
        task.completed_by = by
        task.save(update_fields=["completed_at", "completed_by", "updated_at"])
        audit_record(
            AuditEntry(
                action=AuditAction.HOUSEKEEPING_COMPLETED,
                actor_id=by.pk,
                actor_label=by.display_name,
                target_type="unit",
                target_id=unit.code,
                reason=f"پایان نظافت {unit.code} ({task.assignee})",
                metadata={"task": str(task.pk)},
                request=request,
            )
        )
    return task
