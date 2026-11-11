"""میز پذیرش (P-06): تصویر زنده، چرخهٔ نظافت، جست‌وجوی اسکنر و نگهبان صفحه.

پوشش:
    • قواعد تقدم وضعیت واحد (مقیم ⇐ مسدود ⇐ نظافت ⇐ در انتظار ⇐ آماده) و جای کلید؛
    • مسیرهای دفتر (در انتظار/تأخیر/پذیرش‌شده/گذشته از تخلیه) و پرچم‌های عملیاتی؛
    • تخلیه + سپردن به نظافت «با هم یا هیچ»، پایان نظافت، تکرار ⇒ ۴۰۹، حسابرسی؛
    • جست‌وجو با کد رزرو، QR، کد کوتاه آفلاین، کد ملی (حسابرسی‌شده) و نام؛
    • صفحهٔ ‎/reception/‎: ناشناس و زائر ⇒ ۴۰۴ واقعی، کارکنان ⇒ ۲۰۰ بی‌نمایه؛ سد عامل دوم.
"""

from __future__ import annotations

from datetime import timedelta
from functools import cache
import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.test import Client, override_settings
import pytest
import time_machine
import yaml

from apps.audit.models import AuditRecord
from apps.inventory import housekeeping, services as inventory_services
from apps.inventory.models import HousekeepingTask
from apps.reservation import desk, services as reservation_services
from apps.reservation.certificate import build_certificate
from apps.reservation.choices import ReservationStatus
from tests.factories import days_ahead, lattice_period, make_user, national_id_for

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _book(guest: Any, unit: Any, *, day: int = 2, index: int = 0, cells: int = 2) -> Any:
    start, end = lattice_period(start_day=days_ahead(day), index=index, cells=cells)
    return reservation_services.create_reservation(
        guest=guest, unit=unit, start=start, end=end, guests_count=3
    )


@pytest.fixture
def shift(units, guest, other_guest, operator) -> dict[str, Any]:
    """یک لحظهٔ واقعی شیفت: A1 مقیم، B1 در انتظار، A2 در نظافت، B2 مسدود، B3 آماده."""
    third = make_user(phone="09120000009", first_name="علی", last_name="نوری", city="تبریز")
    resident = _book(guest, units["A1"])
    arriving = _book(other_guest, units["B1"])
    later = _book(third, units["B3"], day=6)
    moment = resident.starts_at + timedelta(minutes=30)
    with time_machine.travel(moment, tick=False):
        reservation_services.check_in(reservation=resident, by=operator)
        housekeeping.start(unit=units["A2"], assignee="برادر صادقی", minutes=60, by=operator)
        inventory_services.create_block(
            unit=units["B2"],
            start=moment - timedelta(hours=1),
            end=moment + timedelta(hours=6),
            kind="maintenance",
            reason="سرویس دوره‌ای فن‌کوئل",
            actor=operator,
        )
    return {"moment": moment, "resident": resident, "arriving": arriving, "later": later}


@cache
def _openapi() -> dict[str, Any]:
    """سند متعهدشدهٔ OpenAPI (برابری‌اش با تولید تازه را ‎test_api_contract‎ تضمین می‌کند)."""
    path = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _desk(api, operator, moment) -> dict[str, Any]:
    api.force_authenticate(user=operator)
    with time_machine.travel(moment, tick=False):
        response = api.get("/api/v1/staff/desk")
    assert response.status_code == 200, response.content
    assert "no-store" in response["Cache-Control"]
    return response.json()


def test_unit_states_follow_the_precedence_rules(api, operator, shift) -> None:
    """هر واحد دقیقاً یک وضعیت دارد و جای کلید از همان وضعیت می‌آید."""
    body = _desk(api, operator, shift["moment"])
    cards = {card["code"]: card for card in body["units"]}
    assert {code: card["state"] for code, card in cards.items()} == {
        "A1": "occupied",
        "B1": "expected",
        "A2": "buffer",
        "B2": "maintenance",
        "B3": "ready",
    }
    assert [cards[c]["key_location"] for c in ("A1", "B1", "A2", "B2", "B3")] == [
        "guest",
        "cabinet",
        "housekeeping",
        "facilities",
        "cabinet",
    ]
    assert cards["A1"]["current"]["code"] == shift["resident"].code
    assert cards["B1"]["arrival"]["desk"]["can_check_in"] is True
    assert cards["B3"]["arrival"]["code"] == shift["later"].code
    assert cards["A2"]["housekeeping"]["assignee"] == "برادر صادقی"
    assert 0 <= cards["A2"]["housekeeping"]["percent"] < 100
    assert cards["B2"]["block"]["reason"] == "سرویس دوره‌ای فن‌کوئل"

    kpis = body["kpis"]
    assert (kpis["occupied"], kpis["ready"], kpis["buffer"], kpis["expected"]) == (1, 1, 1, 1)
    assert kpis["maintenance"] == 1
    assert kpis["guests_in_house"] == 3
    assert body["officer"]["display_name"]
    assert body["shift"]["slot"] in {"morning", "night"}
    assert body["housekeeping"]["presets"] == list(housekeeping.PRESET_MINUTES)
    assert "برادر صادقی" in body["housekeeping"]["suggestions"]
    kinds = {event["kind"] for event in body["events"]}
    assert {"reservation.checked_in", "housekeeping.started", "block.created"} <= kinds


def test_ledger_lanes_and_flags_mirror_the_service_rules(api, operator, shift) -> None:
    """ورودی پس از مهلت ⇒ «تأخیر»؛ پرچم عدم‌حضور فقط پس از فرصت سیاست."""
    body = _desk(api, operator, shift["moment"])
    lanes = {row["code"]: row["desk"]["lane"] for row in body["ledger"]}
    assert lanes[shift["resident"].code] == "checked_in"
    assert lanes[shift["arriving"].code] == "expected"
    row = next(r for r in body["ledger"] if r["code"] == shift["arriving"].code)
    assert row["desk"]["can_no_show"] is False
    assert row["desk"]["short_code"] == build_certificate(shift["arriving"]).short_code
    assert "***" in row["guest_phone_masked"]
    assert row["desk"]["guest_city"] == "مشهد"
    # قرارداد OpenAPI: رزروی که جابه‌جا نشده ‎null‎ می‌دهد و سند هم آن را nullable اعلام می‌کند.
    assert row["superseded_by_code"] is None
    schema = _openapi()["components"]["schemas"]["StaffReservation"]["properties"]
    assert schema["superseded_by_code"].get("nullable") is True

    late = shift["arriving"].starts_at + timedelta(minutes=desk.LATE_MINUTES + 5)
    lanes = {r["code"]: r["desk"]["lane"] for r in _desk(api, operator, late)["ledger"]}
    assert lanes[shift["arriving"].code] == "delayed"

    after_grace = shift["arriving"].starts_at + timedelta(hours=desk.DeskRules.load().grace_hours)
    body = _desk(api, operator, after_grace + timedelta(minutes=1))
    row = next(r for r in body["ledger"] if r["code"] == shift["arriving"].code)
    assert row["desk"]["can_no_show"] is True


def test_overstay_is_flagged_after_the_stay_ends(api, operator, shift) -> None:
    """مقیمی که از پایان اقامتش گذشته، در دفتر «گذشته از تخلیه» است و مقیم می‌ماند."""
    after = shift["resident"].ends_at + timedelta(hours=2)
    body = _desk(api, operator, after)
    row = next(r for r in body["ledger"] if r["code"] == shift["resident"].code)
    assert row["desk"]["lane"] == "overstay"
    assert row["desk"]["is_overstay"] is True
    assert next(u for u in body["units"] if u["code"] == "A1")["state"] == "occupied"


def test_check_in_records_the_handover_checklist_as_the_event_reason(api, operator, shift) -> None:
    """یادداشت چک‌لیست پیشخوان دلیلِ رخداد پذیرش می‌شود؛ بدنهٔ خالی هم پذیرفته است."""
    api.force_authenticate(user=operator)
    code = shift["arriving"].code
    note = "پذیرش با چک‌لیست: هویت ✓، قوانین ✓، کلید ✓"
    with time_machine.travel(shift["arriving"].starts_at + timedelta(minutes=5), tick=False):
        response = api.post(
            f"/api/v1/staff/reservations/{code}/check-in", {"staff_note": note}, format="json"
        )
        assert response.status_code == 200, response.content
        assert response.json()["status"] == ReservationStatus.CHECKED_IN
        event = shift["arriving"].events.get(kind="checked_in")
        assert event.reason == note
        empty = api.post(f"/api/v1/staff/reservations/{shift['later'].code}/check-in")
        assert empty.status_code == 409  # بدنهٔ خالی معتبر است؛ فقط پنجرهٔ پذیرش باز نیست
        assert empty.json()["code"] == "too_early_to_check_in"


def test_checkout_hands_the_unit_to_housekeeping_atomically(api, operator, shift, units) -> None:
    """تخلیه + نظافت با هم؛ پایان نظافت ⇒ آماده؛ تکرار ⇒ ۴۰۹؛ همه حسابرسی‌شده."""
    api.force_authenticate(user=operator)
    code = shift["resident"].code
    with time_machine.travel(shift["moment"] + timedelta(hours=1), tick=False):
        bad = api.post(
            f"/api/v1/staff/reservations/{code}/check-out",
            {"housekeeper": "برادر اکبری", "cleaning_minutes": 5},
            format="json",
        )
        assert bad.status_code == 422
        response = api.post(
            f"/api/v1/staff/reservations/{code}/check-out",
            {"housekeeper": "برادر اکبری", "cleaning_minutes": 45},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.json()["status"] == ReservationStatus.COMPLETED
        task = HousekeepingTask.objects.get(unit=units["A1"], completed_at__isnull=True)
        assert task.reservation_code == code
        assert (task.due_at - task.started_at) == timedelta(minutes=45)
        card = next(u for u in api.get("/api/v1/staff/desk").json()["units"] if u["code"] == "A1")
        assert card["state"] == "buffer"

        twice = api.post(
            "/api/v1/staff/units/A1/housekeeping", {"assignee": "دیگری"}, format="json"
        )
        assert twice.status_code == 409
        assert twice.json()["code"] == "housekeeping_open"

        done = api.post("/api/v1/staff/units/A1/housekeeping/complete")
        assert done.status_code == 200
        assert done.json()["unit"]["state"] == "ready"
        again = api.post("/api/v1/staff/units/A1/housekeeping/complete")
        assert again.status_code == 409
        assert api.post("/api/v1/staff/units/ZZ/housekeeping/complete").status_code == 404

    actions = set(AuditRecord.objects.filter(target_id="A1").values_list("action", flat=True))
    assert {"housekeeping.started", "housekeeping.completed"} <= actions


def test_manual_housekeeping_validates_its_input(api, operator, units) -> None:
    """نام خادم اجباری و مدت در بازهٔ مجاز."""
    api.force_authenticate(user=operator)
    assert (
        api.post(
            "/api/v1/staff/units/B3/housekeeping", {"assignee": " "}, format="json"
        ).status_code
        == 422
    )
    ok = api.post(
        "/api/v1/staff/units/B3/housekeeping",
        {"assignee": "برادر جوادی", "minutes": 90, "note": "تعویض ملافه"},
        format="json",
    )
    assert ok.status_code == 200
    assert ok.json()["unit"]["housekeeping"]["note"] == "تعویض ملافه"


def test_lookup_understands_every_scanner_input(api, operator, shift, other_guest) -> None:
    """کد رزرو، QR، کد کوتاه، کد ملی (حسابرسی) و نام — همه به همان رزرو می‌رسند."""
    api.force_authenticate(user=operator)
    target = shift["arriving"]
    certificate = build_certificate(target)
    national = national_id_for(other_guest.phone)

    def find(query: str) -> dict[str, Any]:
        with time_machine.travel(shift["moment"], tick=False):
            response = api.get("/api/v1/staff/desk/lookup", {"q": query})
        assert response.status_code == 200, response.content
        return response.json()

    cases = {
        target.code: "code",
        certificate.token: "certificate",
        f"https://gam-khedmat.ir/verify/{certificate.token}": "certificate",
        f"{certificate.short_code[:4]}-{certificate.short_code[4:]}": "short_code",
        national: "national_id",
        other_guest.last_name: "query",
    }
    for query, kind in cases.items():
        body = find(query)
        assert body["kind"] == kind, query
        assert target.code in {row["code"] for row in body["matches"]}, query
    assert find(certificate.token)["certificate"]["valid"] is True
    persian = national.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert find(persian)["kind"] == "national_id"
    assert find("   ")["kind"] == "empty"
    assert find("ABCD-EFGH")["matches"] == []
    audited = AuditRecord.objects.filter(
        action="data.sensitive_read", reason="جست‌وجوی پیشخوان با کد ملی"
    )
    assert audited.count() == 2


def test_desk_api_is_staff_only(api, guest, units) -> None:
    """زائر و ناشناس به هیچ نقطهٔ پیشخوان راه ندارند."""
    for path in ("/api/v1/staff/desk", "/api/v1/staff/desk/lookup?q=1"):
        assert api.get(path).status_code in {401, 403}
        api.force_authenticate(user=guest)
        assert api.get(path).status_code == 403
        api.force_authenticate(user=None)
    api.force_authenticate(user=guest)
    assert api.post("/api/v1/staff/units/A1/housekeeping/complete").status_code == 403


# ── صفحهٔ فرانت و سد عامل دوم ───────────────────────────────────────────────

PLACEHOLDER = "__CSP_NONCE__"


@pytest.fixture
def reception_front(tmp_path: Path):
    root = tmp_path / "releases" / "20261111101500-abcdef0"
    (root / "pages").mkdir(parents=True)
    (root / "public" / "_next" / "static").mkdir(parents=True)
    for name, body in {
        "index.html": "HOME",
        "reception.html": "RECEPTION-SHELL",
        "404.html": "NOT-FOUND",
    }.items():
        (root / "pages" / name).write_text(
            f'<main>{body}</main><script nonce="{PLACEHOLDER}"></script>', encoding="utf-8"
        )
    (root / "public" / "_next" / "static" / "app.js").write_text("x", encoding="utf-8")
    (root / "routes.json").write_text(
        json.dumps(
            {
                "version": 1,
                "nonce_placeholder": PLACEHOLDER,
                "not_found": "404.html",
                "routes": {"/": "index.html", "/reception/": "reception.html"},
            }
        ),
        encoding="utf-8",
    )
    (root / "BUILD_INFO.json").write_text(
        json.dumps({"app": "mahdieh-front", "release": root.name, "commit": "abcdef0" * 5}),
        encoding="utf-8",
    )
    current = tmp_path / "current"
    current.symlink_to(Path("releases") / root.name)
    enforced = {"SECURE_CSP": settings.CSP_POLICY_ENFORCED, "SECURE_CSP_REPORT_ONLY": {}}
    with override_settings(MAHDIEH_FRONT_DIR=str(current), **enforced):
        yield


def test_reception_page_is_a_real_404_for_everyone_but_staff(
    reception_front, guest, operator
) -> None:
    """ناشناس و زائر قالب ۴۰۴ با وضعیت واقعی می‌گیرند؛ کارکنان پوسته را، بی‌نمایه و بی‌کش."""
    anonymous = Client().get("/reception/")
    assert anonymous.status_code == 404
    assert b"NOT-FOUND" in anonymous.content
    assert anonymous["X-Robots-Tag"].startswith("noindex")

    pilgrim = Client()
    pilgrim.force_login(guest)
    assert pilgrim.get("/reception/").status_code == 404

    staff = Client()
    staff.force_login(operator)
    page = staff.get("/reception/")
    assert page.status_code == 200
    assert b"RECEPTION-SHELL" in page.content
    assert "no-store" in page["Cache-Control"]
    assert "noindex" in page["X-Robots-Tag"]
    assert "Cookie" in page["Vary"]
    assert staff.get("/").status_code == 200
    assert "/reception/" not in Client().get("/robots.txt").content.decode()


@override_settings(STAFF_MFA_REQUIRED=True)
def test_unverified_staff_reach_the_front_gate_not_the_legacy_page(
    reception_front, operator
) -> None:
    """پیش از تأیید عامل دوم: پوستهٔ پیشخوان و دارایی‌هایش باز، داده بسته، صفحه‌های دیگر ⇒ گیت."""
    staff = Client()
    staff.force_login(operator)
    assert staff.get("/reception/").status_code == 200
    home = staff.get("/")
    assert home.status_code == 302
    assert home["Location"] == "/reception/?next=/"
    data = staff.get("/api/v1/staff/desk")
    assert data.status_code == 403
    assert data.json()["code"] == "mfa_required"
    assert staff.get("/api/v1/auth/mfa/status").status_code == 200
    legacy = staff.get("/board/")
    assert legacy.status_code == 302
    assert legacy["Location"].startswith("/board/mfa/")
    # آخر از همه: بستن پاسخ فایل، سیگنال پایان درخواست را می‌فرستد و اتصال آزمون را می‌بندد.
    asset = staff.get("/_next/static/app.js")
    assert asset.status_code == 200
    assert b"".join(asset.streaming_content) == b"x"
    asset.close()


def test_block_remove_contract_requires_a_reason(api, operator, units) -> None:
    """پایان تعمیرات از پیشخوان: بدنه فقط «دلیل»؛ بی‌دلیل ⇒ ۴۲۲، با دلیل ⇒ ۲۰۴ و حسابرسی."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    api.force_authenticate(user=operator)
    created = api.post(
        "/api/v1/blocks",
        {
            "unit_code": "B3",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "kind": "maintenance",
            "reason": "تعویض قفل درب",
        },
        format="json",
    )
    assert created.status_code == 201
    block_id = created.json()["id"]

    missing = api.post(f"/api/v1/blocks/{block_id}/remove", {}, format="json")
    assert missing.status_code == 422
    assert "reason" in missing.json()["errors"]
    short = api.post(f"/api/v1/blocks/{block_id}/remove", {"reason": "تمام"}, format="json")
    assert short.status_code == 422

    done = api.post(
        f"/api/v1/blocks/{block_id}/remove", {"reason": "تعمیر قفل انجام شد"}, format="json"
    )
    assert done.status_code == 204
    assert AuditRecord.objects.filter(target_type="block", target_id=block_id).count() == 2
