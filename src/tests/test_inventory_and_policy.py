"""تست‌های موجودی و موتور سیاست — واحد، طبقه، بلوک و دادهٔ قواعد."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.core.errors import ConflictError, ValidationError as DomainValidationError
from apps.core.outbox import OutboxEvent
from apps.inventory import services as inventory_services
from apps.inventory.models import Block
from apps.reservation.models import Policy, PolicyRevision
from apps.reservation.policy import (
    DEFAULTS,
    all_policies,
    get_policy,
    lattice_config,
    quiet_hours,
    set_policy,
)
from tests.factories import future_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

User = get_user_model()


# ─── موجودی ───────────────────────────────────────────────────────────────────


def test_units_endpoint_returns_property_layout(api, units) -> None:
    """فهرست واحدها همان ۵ واحد واقعی زائرسرا را برمی‌گرداند."""
    response = api.get("/api/v1/units")
    assert response.status_code == 200
    payload = response.json()
    assert [item["code"] for item in payload] == ["A1", "B1", "A2", "B2", "B3"]
    first = payload[0]
    assert first["type_label"] == "سوئیت"
    assert first["capacity_total"] == 6
    assert first["floor_number"] == 1


def test_unit_detail_and_property_endpoints(api, units) -> None:
    """جزئیات واحد و ساختار طبقات."""
    detail = api.get("/api/v1/units/a1")
    assert detail.status_code == 200
    assert detail.json()["title"] == "سوئیت طبقهٔ اول"

    missing = api.get("/api/v1/units/zz")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    property_view = api.get("/api/v1/property")
    assert property_view.status_code == 200
    floors = property_view.json()
    assert [floor["number"] for floor in floors] == [1, 2, 3]
    assert len(floors[2]["units"]) == 1


def test_block_creation_requires_reason_and_is_audited(units, operator) -> None:
    """بلوک نگهداری بدون دلیل ثبت نمی‌شود و ثبت موفق، حسابرسی دارد (I14)."""
    start, end = future_period(days=10, cells=2)
    with pytest.raises(DomainValidationError):
        inventory_services.create_block(
            start=start, end=end, kind="maintenance", reason="کم", unit=units["A1"], actor=operator
        )

    block = inventory_services.create_block(
        start=start,
        end=end,
        kind="maintenance",
        reason="تعمیر کولر سوئیت یک",
        unit=units["A1"],
        actor=operator,
    )
    assert block.pk is not None
    assert OutboxEvent.all_objects.filter(event_type="block.created").exists()
    record = AuditRecord.all_objects.filter(action=AuditAction.BLOCK_CREATED).first()
    assert record is not None
    assert "تعمیر کولر" in record.reason


def test_overlapping_blocks_on_same_unit_are_rejected(units, operator) -> None:
    """دو بلوک هم‌پوشان روی یک واحد ⇒ خطای دامنه‌ای (قید دیتابیس)."""
    start, end = future_period(days=12, cells=2)
    inventory_services.create_block(
        start=start,
        end=end,
        kind="maintenance",
        reason="نظافت اساسی",
        unit=units["B1"],
        actor=operator,
    )
    with pytest.raises(ConflictError) as excinfo:
        inventory_services.create_block(
            start=start + timedelta(hours=6),
            end=end + timedelta(hours=6),
            kind="cleaning",
            reason="بازرسی تأسیسات",
            unit=units["B1"],
            actor=operator,
        )
    assert excinfo.value.code == "block_overlap"


def test_global_block_covers_all_units(units, operator) -> None:
    """بلوک سراسری (بدون واحد) همهٔ واحدها را بسته می‌کند."""
    start, end = future_period(days=14, cells=2)
    inventory_services.create_block(
        start=start, end=end, kind="event", reason="مأموریت موقت زائرسرا", actor=operator
    )
    assert Block.objects.filter(unit__isnull=True).count() == 1
    assert Block.objects.count() == 1


def test_remove_block_marks_soft_delete_and_audits(units, operator) -> None:
    """برداشتن بلوک: حذف نرم + حسابرسی + رخداد بیرونی."""
    start, end = future_period(days=16, cells=2)
    block = inventory_services.create_block(
        start=start,
        end=end,
        kind="admin",
        reason="بستن موقت برای رویداد",
        unit=units["A2"],
        actor=operator,
    )
    inventory_services.remove_block(block=block, actor=operator, reason="رویداد لغو شد")
    block.refresh_from_db()
    assert block.is_active is False
    assert block.deleted_at is not None
    assert OutboxEvent.all_objects.filter(event_type="block.removed").exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.BLOCK_REMOVED).exists()
    assert Block.objects.filter(pk=block.pk).count() == 0  # مدیر پیش‌فرض فقط زنده‌ها


def test_blocks_endpoint_requires_staff(api, units, guest, operator) -> None:
    """فهرست بلوک‌ها برای زائر بسته و برای کارمند باز است."""
    assert api.get("/api/v1/blocks").status_code in {401, 403}
    api.force_authenticate(user=guest)
    assert api.get("/api/v1/blocks").status_code == 403
    api.force_authenticate(user=operator)
    assert api.get("/api/v1/blocks").status_code == 200


# ─── موتور سیاست ──────────────────────────────────────────────────────────────


def test_policy_defaults_are_used_until_overridden() -> None:
    """تا وقتی داده‌ای ثبت نشده، پیش‌فرض‌های کد پاسخ می‌دهند."""
    assert get_policy("reservation.max_cells") == 14
    assert get_policy("reservation.min_cells") == 1
    assert Policy.objects.count() == 0
    snapshot = {item.key: item.source for item in all_policies()}
    assert set(snapshot.values()) == {"default"}


def test_set_policy_creates_revision_and_takes_effect_immediately(manager) -> None:
    """تغییر سیاست: نسخهٔ تغییرناپذیر + اثر فوری (ابطال کش)."""
    assert get_policy("reservation.max_cells") == DEFAULTS["reservation.max_cells"]
    set_policy(
        key="reservation.max_cells",
        value=7,
        actor=manager,
        reason="محدودسازی موقت ایام اوج",
    )
    assert get_policy("reservation.max_cells") == 7
    policy = Policy.objects.get(key="reservation.max_cells")
    assert policy.version == 2
    revision = PolicyRevision.objects.get(policy=policy)
    assert revision.version == 2
    assert revision.value == 7
    assert OutboxEvent.all_objects.filter(event_type="policy.published").exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.POLICY_CHANGED).exists()


def test_policy_requires_reason_and_valid_key(manager) -> None:
    """دلیل اجباری و کلید ناشناخته رد می‌شود."""
    with pytest.raises(DomainValidationError):
        set_policy(key="reservation.max_cells", value=3, actor=manager, reason="کم")
    with pytest.raises(DomainValidationError):
        set_policy(key="reservation.unknown_key", value=3, actor=manager, reason="دلیل کافی")


def test_policy_values_are_coerced_to_declared_type(manager) -> None:
    """مقدار رشته‌ای عدد به عدد صحیح تبدیل می‌شود (جلوگیری از خطای زمان اجرا)."""
    set_policy(
        key="reservation.booking_horizon_days",
        value="45",
        actor=manager,
        reason="کوتاه‌کردن افق رزرو در فصل اوج",
    )
    assert get_policy("reservation.booking_horizon_days") == 45
    assert isinstance(get_policy("reservation.booking_horizon_days"), int)


def test_lattice_config_follows_policy(manager) -> None:
    """پیکربندی شبکه از سیاست‌ها ساخته می‌شود، نه از مقدار ثابت در کد."""
    set_policy(
        key="lattice.default_cleaning_buffer_minutes",
        value=90,
        actor=manager,
        reason="افزایش زمان نظافت در فصل اوج",
    )
    config = lattice_config()
    assert config.cleaning_buffer_minutes == 90
    assert config.cell_hours == DEFAULTS["lattice.cell_hours"]


def test_quiet_hours_window_wraps_midnight() -> None:
    """ساعات سکوت پیامک (۲۳ تا ۷) درست محاسبه می‌شود."""
    from datetime import datetime

    from django.utils import timezone

    zone = timezone.get_current_timezone()
    assert quiet_hours(datetime(2026, 10, 20, 23, 30, tzinfo=zone)) is True
    assert quiet_hours(datetime(2026, 10, 20, 3, 0, tzinfo=zone)) is True
    assert quiet_hours(datetime(2026, 10, 20, 12, 0, tzinfo=zone)) is False


def test_policy_can_lower_active_limit_and_is_enforced(manager) -> None:
    """تغییر سقف رزرو فعال، در دامنه اثر می‌گذارد (سیاست = داده)."""
    set_policy(
        key="reservation.max_active_per_user",
        value=0,
        actor=manager,
        reason="بستن موقت رزرو آنلاین برای ایام اوج",
    )
    assert get_policy("reservation.max_active_per_user") == 0


def test_audit_chain_stays_valid_after_policy_changes(manager) -> None:
    """زنجیرهٔ حسابرسی پس از تغییرات سیاست سالم می‌ماند (عدم‌انکار)."""
    from apps.audit.services import verify_chain

    set_policy(
        key="reservation.min_lead_time_hours",
        value=4,
        actor=manager,
        reason="آماده‌سازی پیش از ورود",
    )
    ok, detail = verify_chain()
    assert ok, detail
    make_user(phone="09120000099")
