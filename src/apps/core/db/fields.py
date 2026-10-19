"""یکتایی شرطی روی MariaDB با ستون مجازی (جانشین «partial unique index»).

MariaDB ایندکس جزئی (``UNIQUE … WHERE``) ندارد و جنگو ``UniqueConstraint`` شرطی را
روی آن **بی‌صدا نادیده می‌گیرد** (فقط هشدار ``models.W036``) — یعنی قاعدهٔ
«یک درخواست فعال برای هر زائر» بدون هیچ خطایی از بین می‌رفت.

راه‌حل استاندارد: ستون مجازیِ تولیدشده که فقط وقتی شرط برقرار است مقدار دارد و
در غیر این صورت ``NULL`` است؛ ایندکس یکتا روی آن. ``NULL``ها در ایندکس یکتا
تکراری شمرده نمی‌شوند، پس دقیقاً همان معنای ایندکس جزئی به‌دست می‌آید. این
الگو پیش از تصمیم روی خودِ میزبان سنجیده شد (مرحلهٔ ۴: ``conditional_unique``).
"""

from __future__ import annotations

from typing import Any

from django.db import models
from django.db.models import Case, F, Q, When


def conditional_key(source: str, condition: Q, output_field: models.Field[Any, Any]) -> Any:
    """ستون مجازی «``source`` اگر ``condition``، وگرنه NULL» برای یکتایی شرطی."""
    return models.GeneratedField(
        expression=Case(When(condition, then=F(source)), default=None),
        output_field=output_field,
        db_persist=False,
    )
