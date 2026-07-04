"""Конвертация HTML-фрагмента тела статьи в чистый markdown (SPEC §7).

Сохраняем: заголовки, списки, цитаты, таблицы, ссылки, блоки кода (с языком),
картинки (alt + src). Вырезаем: рекламные вставки, служебную обвязку.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

# Классы/элементы служебной обвязки, которые режем перед конвертацией.
_JUNK_CLASS_SUBSTRINGS = (
    "code-explainer",     # рекламный блок «объяснить код» под сниппетами
    "tm-button",
    "sharing",
    "share",
    "advertisement",
    "banner",
)


def _strip_junk(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if not getattr(tag, "attrs", None):
            continue
        classes = tag.attrs.get("class") or []
        blob = " ".join(classes).lower()
        if any(sub in blob for sub in _JUNK_CLASS_SUBSTRINGS):
            tag.decompose()


class _HabrConverter(MarkdownConverter):
    """markdownify с языком в ограждённых блоках кода."""

    def convert_pre(self, el, text, parent_tags):  # noqa: N802 (markdownify API)
        if not text:
            return ""
        code = el.find("code")
        language = ""
        if code is not None:
            for cls in code.get("class", []) or []:
                # На Хабре язык лежит в class="python" / class="bash" и т.п.
                if cls not in ("hljs",):
                    language = cls
                    break
        text = text.rstrip("\n")
        return f"\n\n```{language}\n{text}\n```\n\n"


def html_to_markdown(html_fragment: str) -> str:
    """HTML-фрагмент → markdown-строка."""
    soup = BeautifulSoup(html_fragment or "", "html.parser")
    _strip_junk(soup)
    md = _HabrConverter(
        heading_style="ATX",
        bullets="-",
        strip=["button"],
    ).convert_soup(soup)
    # Схлопываем лишние пустые строки (>2 подряд → ровно 2).
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def word_count(markdown_text: str) -> int:
    """Грубая оценка числа слов в теле (для frontmatter word_count)."""
    return len(re.findall(r"\w+", markdown_text, flags=re.UNICODE))
