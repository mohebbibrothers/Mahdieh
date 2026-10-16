"""نمای «میز راهنما» — پاسخ‌های آمادهٔ زائر با جست‌وجوی فارسی.

سه تصمیم طراحی:
    ۱. **بی‌جاوااسکریپت هم کار می‌کند**: جست‌وجو یک فرم ``GET`` ساده است و
       پاسخ‌ها با ``<details>`` باز و بسته می‌شوند. اگر جاوااسکریپت کاربر
       بارگذاری نشود، هیچ قابلیتی از دست نمی‌رود.
    ۲. **راهنما از موتور نمی‌افتد**: همهٔ عددها از ``policy_values`` می‌آید،
       پس سهمیه و ساعت‌ها همیشه همان چیزی است که موتور اجرا می‌کند.
    ۳. **حالت چاپ**: یک صفحهٔ مستقل و خوانا برای چاپ و نصب روی دیوار پذیرش.
"""

from __future__ import annotations

import json
from typing import Any, Final

from django.views.generic import TemplateView

from apps.web import help_content

MAX_SEARCH_RESULTS: Final[int] = 6

# نویسه‌هایی که در ``<script type="application/ld+json">`` باید فرار کنند تا
# داده‌ی ساخت‌یافته هرگز به تزریق HTML بدل نشود (متن ماست، ولی قاعده ثابت است).
JSON_LD_ESCAPES: Final[dict[str, str]] = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def _json_ld(payload: list[dict[str, str]]) -> str:
    """بستهٔ ``FAQPage`` برای موتور جست‌وجو و صفحه‌خوان — خروجی ایمن و معتبر."""
    body = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "inLanguage": "fa",
        "mainEntity": [
            {
                "@type": "Question",
                "name": entry["question"],
                "acceptedAnswer": {"@type": "Answer", "text": entry["answer"]},
            }
            for entry in payload
        ],
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    for needle, replacement in JSON_LD_ESCAPES.items():
        raw = raw.replace(needle, replacement)
    return raw


def _decorate(item: help_content.HelpItem, values: dict[str, str]) -> dict[str, Any]:
    """آماده‌سازی یک پرسش برای قالب: متن پرشده، دکمه و پاسخ‌های مرتبط.

    چرا در نما و نه در قالب؟ چون قالب جنگو تابع با آرگومان صدا نمی‌زند؛ منطق
    «پاسخ مرتبط را پیدا کن» جای درستش لایهٔ نمایش است، نه فیلتر قالب.
    """
    return {
        "slug": item.slug,
        "question": item.question,
        "answer": help_content.render(item.answer, values),
        "steps": tuple(help_content.render(step, values) for step in item.steps),
        "action": item.action,
        "action_url": help_content.action_url(item.action),
        "related": help_content.related_items(item),
        "url": help_content.item_url(item),
    }


def _decorate_category(
    category: help_content.HelpCategory, values: dict[str, str]
) -> dict[str, Any]:
    """آماده‌سازی یک دسته با همهٔ پرسش‌هایش."""
    return {
        "slug": category.slug,
        "title": category.title,
        "icon": category.icon,
        "summary": category.summary,
        "items": [_decorate(item, values) for item in category.items],
    }


class HelpCenterView(TemplateView):
    """میز راهنما — جست‌وجو، دسته‌ها، پاسخ‌ها، گام‌های نخست و اطلاعات تماس."""

    template_name = "web/help.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ راهنما: نتیجهٔ جست‌وجو (اگر پرسشی آمده) یا همهٔ دسته‌ها."""
        context = super().get_context_data(**kwargs)
        values = help_content.policy_values()
        faq = help_content.faq_payload(values=values)
        query = str(self.request.GET.get("q") or "").strip()
        active = str(self.request.GET.get("cat") or "").strip()

        matches = help_content.search(query, values=values, limit=MAX_SEARCH_RESULTS)
        categories = [
            _decorate_category(category, values)
            for category in help_content.CATEGORIES
            if not active or category.slug == active
        ]
        context.update(
            {
                "query": query,
                "active_category": active,
                "searched": bool(query),
                "matches": [
                    {
                        "item": _decorate(match.item, values),
                        "category": match.category.title,
                        "category_slug": match.category.slug,
                        "score": match.score,
                        "snippet": match.snippet,
                    }
                    for match in matches
                ],
                "categories": categories,
                "category_chips": [
                    {
                        "slug": category.slug,
                        "title": category.title,
                        "icon": category.icon,
                        "count": len(category.items),
                    }
                    for category in help_content.CATEGORIES
                ],
                "quick_steps": help_content.QUICK_STEPS,
                "values": values,
                "contact": help_content.contact(),
                "stats": help_content.stats(values),
                "note": help_content.SITE_HELP_NOTE,
                "faq_payload": faq,
                "faq_jsonld": _json_ld(faq),
            }
        )
        return context


class HelpGuideView(TemplateView):
    """راهنمای چاپی — یک برگ برای زائر و یک برگ چک‌لیست برای میز پذیرش."""

    template_name = "web/help_guide.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """زمینهٔ برگ چاپی: همهٔ دسته‌ها + فهرست گام‌های کارکنان."""
        context = super().get_context_data(**kwargs)
        values = help_content.policy_values()
        context.update(
            {
                "values": values,
                "categories": [
                    _decorate_category(category, values) for category in help_content.CATEGORIES
                ],
                "quick_steps": help_content.QUICK_STEPS,
                "staff_checklist": help_content.STAFF_CHECKLIST,
                "contact": help_content.contact(),
                "today": help_content.today_label(),
            }
        )
        return context
