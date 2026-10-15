"""پیکربندی ریشهٔ pytest — افزودن ``src`` به مسیر واردات و ابزارهای مشترک."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

SRC = Path(__file__).parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """بک‌اند async برای تست‌های آینده."""
    return "asyncio"
