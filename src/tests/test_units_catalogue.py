"""قرارداد کاتالوگ واحدها (P-02) — ‎/api/v1/units‎، ‎/units/<code>‎، ‎/property‎ و کشف از تاریخ دلخواه.

صفحهٔ واحدها فقط چیزی را نشان می‌دهد که سرور ثبت کرده است: امکانات فعال (با یادداشت ویژهٔ
همان واحد)، تصویر شاخص در صورت وجود و یادداشت دسترسی طبقه. یادداشت داخلی کارکنان هرگز
از API عمومی بیرون نمی‌رود و واحد غیرفعال برای عموم «وجود ندارد».
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
import pytest
import time_machine

from apps.inventory.models import UnitFeature, UnitFeatureLink, UnitPhoto
from tests.factories import days_ahead

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

WINDOWS = "/api/v1/calendar/windows"


def _feature(code: str, *, title: str, highlight: bool = False, order: int = 1) -> UnitFeature:
    """ساخت امکان با ترتیب نمایش."""
    return UnitFeature.objects.create(
        code=code, title=title, icon="", is_highlight=highlight, sort_order=order
    )


def test_staff_notes_never_leak_and_inactive_unit_is_hidden(api, units) -> None:
    """یادداشت کارکنان در جزئیات عمومی نیست؛ واحد غیرفعال ⇒ ۴۰۴ و حذف از فهرست و طبقات."""
    unit = units["A1"]
    unit.notes = "کلید یدک نزد نگهبان — داخلی"
    unit.save(update_fields=["notes"])

    detail = api.get("/api/v1/units/A1")
    assert detail.status_code == 200
    body = detail.json()
    assert "notes" not in body
    assert "نگهبان" not in detail.content.decode()
    assert body["photos"] == []
    assert body["cover"] is None

    units["B3"].is_active = False
    units["B3"].save(update_fields=["is_active"])
    assert api.get("/api/v1/units/B3").status_code == 404
    assert "B3" not in [row["code"] for row in api.get("/api/v1/units").json()]
    floors = api.get("/api/v1/property").json()
    assert floors[2]["units"] == []


def test_features_are_active_ordered_and_carry_unit_note(api, units) -> None:
    """فقط امکانات فعال، شاخص‌ها نخست، با یادداشت ویژهٔ همان واحد — در فهرست، جزئیات و طبقات."""
    wifi = _feature("wifi", title="اینترنت", highlight=True, order=1)
    bath = _feature("private-bath", title="سرویس اختصاصی", highlight=True, order=2)
    locker = _feature("locker", title="صندوق امانات", order=9)
    retired = _feature("tv", title="تلویزیون", order=3)
    retired.is_active = False
    retired.save(update_fields=["is_active"])
    unit = units["A1"]
    for feature in (locker, retired, bath, wifi):
        UnitFeatureLink.objects.create(
            unit=unit, feature=feature, note="داخل واحد" if feature is bath else ""
        )

    listed = next(row for row in api.get("/api/v1/units").json() if row["code"] == "A1")
    detail = api.get("/api/v1/units/a1").json()
    nested = api.get("/api/v1/property").json()[0]["units"][0]
    for payload in (listed, detail, nested):
        assert [f["code"] for f in payload["features"]] == ["wifi", "private-bath", "locker"]
        assert payload["features"][1]["note"] == "داخل واحد"
        assert payload["features"][0]["note"] == ""


def test_cover_photo_prefers_flagged_image(api, units, settings, tmp_path) -> None:
    """تصویر شاخص (یا نخستین تصویر) به‌صورت ‎cover‎ در کارت می‌آید؛ متن جایگزین پیش‌فرض دارد."""
    settings.MEDIA_ROOT = tmp_path
    unit = units["B1"]
    gif = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    UnitPhoto.objects.create(
        unit=unit, image=SimpleUploadedFile("first.gif", gif), sort_order=0, alt_text=""
    )
    UnitPhoto.objects.create(
        unit=unit,
        image=SimpleUploadedFile("cover.gif", gif),
        sort_order=5,
        is_cover=True,
        alt_text="نمای اتاق",
    )
    listed = next(row for row in api.get("/api/v1/units").json() if row["code"] == "B1")
    assert listed["cover"]["url"].endswith(".gif")
    assert "cover" in listed["cover"]["url"]
    assert listed["cover"]["alt_text"] == "نمای اتاق"
    detail = api.get("/api/v1/units/B1").json()
    assert detail["photos"][0]["is_cover"] is True
    assert len(detail["photos"]) == 2

    UnitPhoto.objects.filter(is_cover=True).delete()
    fallback = next(row for row in api.get("/api/v1/units").json() if row["code"] == "B1")
    assert fallback["cover"]["alt_text"] == "تصویر اتاق بزرگ طبقهٔ اول"


def test_catalogue_queries_do_not_grow_with_units(api, units) -> None:
    """فهرست و طبقات بی‌N+1: تعداد کوئری مستقل از تعداد واحدهاست."""
    for code in units:
        UnitFeatureLink.objects.create(
            unit=units[code],
            feature=UnitFeature.objects.get_or_create(code="wifi", defaults={"title": "اینترنت"})[
                0
            ],
        )
    with CaptureQueriesContext(connection) as listed:
        assert api.get("/api/v1/units").status_code == 200
    with CaptureQueriesContext(connection) as floors:
        assert api.get("/api/v1/property").status_code == 200
    # واحد + امکانات + تصاویر (± نشست/میان‌افزار) — هرگز به‌ازای هر واحد.
    assert len(listed.captured_queries) <= 6
    # طبقه + واحد + امکانات + تصاویر.
    assert len(floors.captured_queries) <= 7


def _frozen_morning() -> datetime:
    """ساعت ۰۹:۰۰ محلی سه روز بعد — مهلت ۲ساعته ⇒ زودترین آغاز سانس شب همان روز."""
    day = days_ahead(3)
    return timezone.make_aware(datetime(day.year, day.month, day.day, 9, 0))


def test_windows_from_date_starts_search_at_requested_half_day(api, units) -> None:
    """‎from/period‎: جست‌وجو از همان نیم‌روز آغاز می‌شود؛ زودتر از مهلت پیش از ورود هرگز."""
    with time_machine.travel(_frozen_morning(), tick=False):
        today = timezone.localdate()
        target = today + timedelta(days=6)
        body = api.get(WINDOWS, {"from": target.isoformat(), "period": "night"}).json()
        expected = timezone.make_aware(datetime(target.year, target.month, target.day, 20, 0))
        assert datetime.fromisoformat(body["not_before"]) == expected
        for row in body["units"]:
            assert datetime.fromisoformat(row["window"]["start"]) == expected
            assert row["window"]["period"] == "night"

        # امروز صبح گذشته است ⇒ مهلت ورود حاکم می‌ماند (سانس شب امروز).
        early = api.get(WINDOWS, {"from": today.isoformat()}).json()
        assert datetime.fromisoformat(early["not_before"]) == timezone.now() + timedelta(hours=2)
        assert early["units"][0]["window"]["period"] == "night"


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"from": "1405-07-30"}, "invalid_from"),
        ({"from": "2020-01-01"}, "invalid_from"),
        ({"from": "2999-01-01"}, "invalid_from"),
        ({"from": "TODAY", "period": "noon"}, "invalid_period"),
    ],
)
def test_windows_from_rejects_bad_values(api, units, params, code) -> None:
    """تاریخ نادرست یا بیرون از افق و نیم‌روز ناشناخته ⇒ ۴۲۲ با کد پایدار."""
    if params.get("from") == "TODAY":
        params = {**params, "from": timezone.localdate().isoformat()}
    response = api.get(WINDOWS, params)
    assert response.status_code == 422
    assert response.json()["code"] == code
