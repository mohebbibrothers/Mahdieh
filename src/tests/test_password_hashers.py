"""هش رمز در تولید — الگوریتم تولید باید واقعاً کار کند، نه فقط در تنظیمات نوشته شود.

درس واقعی (گام ۸ استقرار): ``PASSWORD_HASHERS`` با Argon2 شروع می‌شد ولی
``argon2-cffi`` نصب نبود؛ آزمون‌ها با MD5 سبز بودند و فقط ساخت مدیر روی میزبان
شکست. این آزمون‌ها همان زنجیرهٔ تولید را می‌آزمایند و نگهبانِ دروازهٔ انتشار را.
"""

from __future__ import annotations

import importlib
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, get_hasher, make_password
from django.test import override_settings
import pytest

from apps.core.checks import HASHER_LIBRARY_ERROR, password_hasher_library_check

pytestmark = pytest.mark.unit

PRODUCTION_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]


def test_base_settings_still_lead_with_argon2() -> None:
    from config.settings import base

    assert base.PASSWORD_HASHERS == PRODUCTION_HASHERS
    assert settings.PASSWORD_HASHERS != PRODUCTION_HASHERS  # آزمون‌ها MD5 سریع دارند


@override_settings(PASSWORD_HASHERS=PRODUCTION_HASHERS)
def test_production_hasher_round_trips_a_password() -> None:
    encoded = make_password("Zx9!kQ2#mLp7Rv")
    assert encoded.startswith("argon2$")
    assert check_password("Zx9!kQ2#mLp7Rv", encoded)
    assert not check_password("wrong-password", encoded)
    assert get_hasher().algorithm == "argon2"


@override_settings(PASSWORD_HASHERS=PRODUCTION_HASHERS)
def test_gate_is_green_when_every_hasher_library_loads() -> None:
    assert password_hasher_library_check() == []


@override_settings(PASSWORD_HASHERS=PRODUCTION_HASHERS)
def test_gate_blocks_a_missing_hasher_library(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import_module = importlib.import_module

    def import_module(name: str, package: str | None = None) -> Any:
        if name == "argon2" or name.startswith("argon2."):
            raise ImportError("No module named 'argon2'")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", import_module)
    issues = password_hasher_library_check()
    assert [issue.id for issue in issues] == [HASHER_LIBRARY_ERROR]
    assert "argon2" in issues[0].msg
    assert issues[0].level >= 40  # Error ⇒ production_check انتشار را می‌بندد
