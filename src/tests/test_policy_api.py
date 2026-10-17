"""تست‌های API و پنل سیاست‌ها — خواندن برای کارکنان، تغییر فقط برای مدیر.

سیاست‌ها «تنظیمات زندهٔ» زائرسرا هستند؛ پس دو چیز باید غیرقابل‌عبور بماند:
    • زائر هرگز سیاست نمی‌بیند و تغییر نمی‌دهد.
    • کارمند می‌بیند اما تغییر نمی‌دهد؛ تغییر بدون دلیل هم ثبت نمی‌شود.
"""

from __future__ import annotations

import pytest

from apps.reservation.models import Policy, PolicyRevision
from apps.reservation.policy import DEFAULTS, LABELS, REASON_MIN_LENGTH, get_policy

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_policy_list_is_staff_only(api, guest, operator) -> None:
    """فهرست سیاست‌ها: زائر ۴۰۳، کارمند ۲۰۰."""
    api.force_authenticate(user=guest)
    assert api.get("/api/v1/staff/policies").status_code == 403

    api.force_authenticate(user=operator)
    response = api.get("/api/v1/staff/policies")
    assert response.status_code == 200
    items = response.json()["results"]
    assert len(items) == len(DEFAULTS)
    assert all(item["label"] for item in items)
    assert all(item["type"] in {"بولین", "عدد", "متن"} for item in items)


def test_policy_detail_exposes_effective_and_default(api, operator, manager) -> None:
    """جزئیات یک سیاست: مقدار مؤثر، پیش‌فرض و «تغییر کرده یا نه»."""
    api.force_authenticate(user=operator)
    response = api.get("/api/v1/staff/policies/reservation.max_cells")
    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] == DEFAULTS["reservation.max_cells"]
    assert payload["default"] == DEFAULTS["reservation.max_cells"]
    assert payload["changed"] is False
    assert payload["version"] == 1

    api.force_authenticate(user=manager)
    updated = api.post(
        "/api/v1/staff/policies/reservation.max_cells",
        {"value": 6, "reason": "کوتاه‌کردن اقامت در ایام عادی"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.json()["value"] == 6
    assert get_policy("reservation.max_cells") == 6

    operator_view = api.get("/api/v1/staff/policies/reservation.max_cells")
    assert operator_view.json()["changed"] is True
    assert operator_view.json()["reason"] == "کوتاه‌کردن اقامت در ایام عادی"


def test_policy_write_requires_manager_and_reason(api, operator, manager) -> None:
    """نوشتن: کارمند ۴۰۳، مدیر بدون دلیل ۴۰۰، مدیر با دلیل ۲۰۰."""
    api.force_authenticate(user=operator)
    denied = api.post(
        "/api/v1/staff/policies/reservation.max_cells",
        {"value": 5, "reason": "تلاش کارمند برای تغییر"},
        format="json",
    )
    assert denied.status_code == 403

    api.force_authenticate(user=manager)
    short_reason = api.post(
        "/api/v1/staff/policies/reservation.max_cells",
        {"value": 5, "reason": "خ"[: REASON_MIN_LENGTH - 1]},
        format="json",
    )
    assert short_reason.status_code == 422
    assert "reason" in short_reason.json()["errors"]

    ok = api.post(
        "/api/v1/staff/policies/reservation.max_cells",
        {"value": 5, "reason": "هماهنگی با ظرفیت واقعی"},
        format="json",
    )
    assert ok.status_code == 200


def test_unknown_policy_is_404_for_read_and_400_for_write(api, manager) -> None:
    """کلید ناشناخته: خواندن ۴۰۴، نوشتن خطای اعتبارسنجی."""
    api.force_authenticate(user=manager)
    assert api.get("/api/v1/staff/policies/not.a.policy").status_code == 404
    response = api.post(
        "/api/v1/staff/policies/not.a.policy",
        {"value": 1, "reason": "آزمون کلید ناموجود"},
        format="json",
    )
    assert response.status_code in {400, 422}
    assert response.json()["code"] == "unknown_policy"


def test_policy_value_type_is_coerced(api, manager) -> None:
    """نوع مقدار با نوع پیش‌فرض هم‌سان می‌شود (رشته «۰/» بولی نمی‌شود)."""
    api.force_authenticate(user=manager)
    response = api.post(
        "/api/v1/staff/policies/reservation.holds_enabled",
        {"value": "false", "reason": "خاموش‌کردن نگه‌داشت موقت"},
        format="json",
    )
    assert response.status_code == 200
    assert get_policy("reservation.holds_enabled") is False


def test_every_change_leaves_a_revision(api, manager) -> None:
    """هر تغییر، یک نسخهٔ تاریخی با دلیل و تغییردهنده می‌گذارد."""
    api.force_authenticate(user=manager)
    for value in (5, 6, 7):
        api.post(
            "/api/v1/staff/policies/reservation.max_cells",
            {"value": value, "reason": f"تنظیم تجربی مقدار {value}"},
            format="json",
        )
    revisions = PolicyRevision.objects.filter(policy__key="reservation.max_cells").order_by(
        "version"
    )
    assert revisions.count() == 3
    assert [revision.value for revision in revisions] == [5, 6, 7]
    assert all(revision.changed_by_id == manager.id for revision in revisions)
    assert Policy.objects.get(key="reservation.max_cells").version == 4


def test_policy_board_is_readable_by_staff_and_editable_by_manager(
    client, operator, manager
) -> None:
    """پنل سیاست‌ها: کارمند می‌بیند، مدیر می‌تواند تغییر دهد."""
    client.force_login(operator)
    page = client.get("/board/policies/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "سیاست" in body
    assert "reservation.max_cells" in body

    change = client.post(
        "/board/policies/apply/",
        {
            "key": "reservation.max_cells",
            "value": "4",
            "reason": "کاهش اقامت در ایام عادی",
        },
    )
    assert change.status_code == 302
    assert get_policy("reservation.max_cells") == DEFAULTS["reservation.max_cells"]

    client.force_login(manager)
    applied = client.post(
        "/board/policies/apply/",
        {"key": "reservation.max_cells", "value": "4", "reason": "کاهش اقامت در ایام عادی"},
    )
    assert applied.status_code == 302
    assert get_policy("reservation.max_cells") == 4


def test_policy_board_rejects_short_reason(client, manager) -> None:
    """دلیل کوتاه در پنل هم رد می‌شود (قاعده یک‌جاست، دو مسیر)."""
    client.force_login(manager)
    client.post(
        "/board/policies/apply/",
        {"key": "reservation.max_cells", "value": "4", "reason": "کم"},
    )
    assert get_policy("reservation.max_cells") == DEFAULTS["reservation.max_cells"]


def test_labels_cover_every_policy_key() -> None:
    """هر کلید سیاست، برچسب و توضیح فارسی دارد — پنل هرگز کلید خام نشان نمی‌دهد."""
    assert set(LABELS) == set(DEFAULTS)
    for key, (label, help_text) in LABELS.items():
        assert label and label != key
        assert help_text
