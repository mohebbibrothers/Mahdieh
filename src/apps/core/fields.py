"""فیلدهای سفارشی مدل — شفافیت رمزنگاری در لایهٔ داده.

قاعده: توسعه‌دهنده نباید برای محافظت از دادهٔ حساس «یادش برود» رمزنگاری کند.
با ``EncryptedCharField``، نوشتن و خواندن خودکار رمزنگاری/رمزگشایی می‌شود و
متن روشن هرگز روی دیسک، در پارتیشن WAL یا در بکاپ نمی‌نشیند.

نکتهٔ معماری (و درس گران‌قیمت این پروژه):
    ``max_length`` این فیلد یعنی «حداکثر طول **متن روشن**» و **دست‌نخورده** در
    وضعیت مهاجرت‌ها ثبت می‌شود. بزرگ‌کردن این مقدار در ``__init__`` باعث
    «رانش بی‌پایان مهاجرت» می‌شود (هر ``makemigrations`` یک ALTER تازه می‌سازد،
    چون وضعیت قبلی هم دوباره بزرگ می‌شود: ۳۲ → ۲۵۶ → ۱۱۵۲ → …). پس گسترش طول
    ستون تنها در ``db_parameters`` انجام می‌شود و وضعیت مدل پایدار می‌ماند.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from apps.core.crypto import decrypt_value, encrypt_value

DEFAULT_PLAINTEXT_MAX_LENGTH = 255


class EncryptedCharField(models.CharField):  # type: ignore[type-arg]
    """فیلد متنی که مقدار آن در دیتابیس رمزنگاری‌شده ذخیره می‌شود.

    نکات مهم:
        • ``max_length`` روی **مقدار متن روشن** اعمال می‌شود و همان در وضعیت
          مهاجرت‌ها می‌نشیند؛ طول واقعی ستون به‌صورت شفاف بزرگ‌تر است.
        • جست‌وجوی ``exact`` روی این فیلد بی‌معناست (هر رمزنگاری nonce تازه
          دارد)؛ برای یکتایی/جست‌وجو از ایندکس کور (``blind_index``) استفاده کنید.
    """

    description = "متن رمزنگاری‌شده با AES-GCM (متن روشن هرگز ذخیره نمی‌شود)"
    _EXPANSION_FACTOR = 4
    _BASE_OVERHEAD = 128

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """ثبت ``plaintext_max_length`` بدون دست‌زدن به ``max_length``."""
        declared = kwargs.get("max_length") or DEFAULT_PLAINTEXT_MAX_LENGTH
        self.plaintext_max_length: int = int(declared)
        super().__init__(*args, **kwargs)

    @property
    def storage_max_length(self) -> int:
        """طول ستون دیتابیس: بدنهٔ base64 رمزنگاری‌شده + متادادهٔ کلید."""
        return self.plaintext_max_length * self._EXPANSION_FACTOR + self._BASE_OVERHEAD

    def db_type(self, connection: Any) -> str:
        """نوع ستون دیتابیس با طول بزرگ‌شده (وضعیت مهاجرت دست‌نخورده می‌ماند).

        ``Field.db_parameters()`` نوع را از همین متد می‌گیرد، پس هم ``CREATE
        TABLE`` و هم ``ADD COLUMN`` طول بزرگ‌شده را می‌بینند؛ در حالی که
        ``max_length`` مدل (که در فایل مهاجرت نوشته می‌شود) ثابت است.
        """
        del connection
        return f"varchar({self.storage_max_length})"

    def get_prep_value(self, value: str | None) -> str | None:
        """رمزنگاری پیش از ارسال به دیتابیس."""
        return encrypt_value(super().get_prep_value(value))

    def from_db_value(self, value: str | None, expression: Any, connection: Any) -> str | None:
        """رمزگشایی هنگام خواندن از دیتابیس."""
        del expression, connection
        if value is None:
            return None
        return decrypt_value(value)
