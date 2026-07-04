"""Извлечение метаданных из JSON API и рендер markdown-файлов (SPEC §6).

Собирает из ответа API объект Article, строит YAML-frontmatter вручную
(чтобы не тянуть PyYAML) и тело статьи, а также файл комментариев.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from .convert import html_to_markdown, word_count

MSK = timezone(timedelta(hours=3))


def _strip_tags(s: str | None) -> str:
    if not s:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _article_url(article_id: str) -> str:
    return f"https://habr.com/ru/articles/{article_id}/"


def _extract_modified(metadata: dict | None) -> str | None:
    """dateModified из schema.org JSON-LD внутри metadata (top-level поля нет)."""
    if not metadata:
        return None
    raw = metadata.get("schemaJsonLd")
    if not raw:
        return None
    try:
        return json.loads(raw).get("dateModified")
    except (json.JSONDecodeError, TypeError):
        return None


def _map_type(post_type: str | None) -> str:
    pt = (post_type or "").lower()
    if pt == "news":
        return "news"
    if "mega" in pt:
        return "megapost"
    return "article"


@dataclass
class Article:
    id: str
    url: str
    title: str
    author: str
    author_url: str
    published: str | None
    hubs: list[str]
    tags: list[str]
    company: str | None
    type: str
    translation: bool
    original_url: str | None
    rating: int | None
    views: int | None
    comments_count: int | None
    bookmarks: int | None
    reading_time_min: int | None
    complexity: str | None
    fmt: str | None
    source: str
    modified: str | None
    body_md: str
    word_count: int = 0
    parsed_at: str = ""

    @classmethod
    def from_api(cls, data: dict, *, parsed_at: str) -> "Article":
        article_id = str(data.get("id"))
        stats = data.get("statistics") or {}
        author = data.get("author") or {}
        alias = author.get("alias") or ""

        hubs = [h.get("alias") or _strip_tags(h.get("title")) for h in data.get("hubs") or []]
        tags = [_strip_tags(t.get("titleHtml") or t.get("title")) for t in data.get("tags") or []]
        tags = [t for t in tags if t]

        company = None
        if data.get("isCorporative"):
            for h in data.get("hubs") or []:
                if h.get("type") == "corporate":
                    company = _strip_tags(h.get("title"))
                    break

        # Перевод: помечается меткой в postLabels либо связанным переводом.
        labels = data.get("postLabels") or []
        translation = any(
            "translat" in json.dumps(lbl, ensure_ascii=False).lower() for lbl in labels
        ) or bool(data.get("linkedPostTranslation"))
        original_url = None
        tr_data = data.get("translationData") or {}
        if isinstance(tr_data, dict):
            original_url = tr_data.get("originalUrl") or tr_data.get("originalUrlText")

        body_md = html_to_markdown(data.get("textHtml") or "")

        return cls(
            id=article_id,
            url=_article_url(article_id),
            title=_strip_tags(data.get("titleHtml")),
            author=alias,
            author_url=f"https://habr.com/ru/users/{alias}/" if alias else "",
            published=data.get("timePublished"),
            hubs=hubs,
            tags=tags,
            company=company,
            type=_map_type(data.get("postType")),
            translation=translation,
            original_url=original_url,
            rating=stats.get("score"),
            views=stats.get("readingCount"),
            comments_count=stats.get("commentsCount"),
            bookmarks=stats.get("favoritesCount"),
            reading_time_min=data.get("readingTime"),
            complexity=data.get("complexity"),
            fmt=data.get("format"),
            source="api",
            modified=_extract_modified(data.get("metadata")),
            body_md=body_md,
            word_count=word_count(body_md),
            parsed_at=parsed_at,
        )


# ---- YAML-frontmatter (пишем вручную, без PyYAML) -------------------------

def _yaml_str(value: str | None) -> str:
    if value is None:
        return "null"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(_yaml_str(v) for v in values) + "]"


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_str(str(value))


def _yaml_raw(value: str | None) -> str:
    """Для ISO-дат: валидный YAML-таймстамп без кавычек."""
    return value if value else "null"


def render_article(article: Article) -> str:
    a = article
    lines = [
        "---",
        "# — базовые —",
        f"id: {a.id}",
        f"url: {a.url}",
        f"title: {_yaml_str(a.title)}",
        f"author: {_yaml_str(a.author)}",
        f"author_url: {a.author_url}",
        f"published: {_yaml_raw(a.published)}",
        "# — классификация —",
        f"hubs: {_yaml_list(a.hubs)}",
        f"tags: {_yaml_list(a.tags)}",
        f"company: {_yaml_str(a.company)}",
        f"type: {a.type}",
        f"format: {_yaml_scalar(a.fmt)}",
        f"complexity: {_yaml_scalar(a.complexity)}",
        f"translation: {_yaml_scalar(a.translation)}",
        f"original_url: {_yaml_str(a.original_url)}",
        "# — метрики (public) —",
        f"rating: {_yaml_scalar(a.rating)}",
        f"views: {_yaml_scalar(a.views)}",
        f"comments_count: {_yaml_scalar(a.comments_count)}",
        f"bookmarks: {_yaml_scalar(a.bookmarks)}",
        "# — производные —",
        f"word_count: {a.word_count}",
        f"reading_time_min: {_yaml_scalar(a.reading_time_min)}",
        "# — служебное —",
        f"source: {a.source}",
        f"parsed_at: {_yaml_raw(a.parsed_at)}",
        f"modified: {_yaml_raw(a.modified)}",
        "---",
        "",
        f"# {a.title}",
        "",
        a.body_md,
        "",
    ]
    return "\n".join(lines)


# ---- Комментарии ----------------------------------------------------------

def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MSK)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return iso


def render_comments(article: Article, comments_payload: dict) -> str | None:
    """Плоский dict комментариев Хабра → вложенный markdown-список (SPEC §6)."""
    comments = comments_payload.get("comments") or {}
    if not comments:
        return None

    # Строим дерево по parentId.
    children: dict[str | None, list[dict]] = {}
    for c in comments.values():
        children.setdefault(c.get("parentId"), []).append(c)
    for bucket in children.values():
        bucket.sort(key=lambda c: c.get("timePublished") or "")

    out: list[str] = []

    def walk(parent_id, depth):
        for c in children.get(parent_id, []):
            author = (c.get("author") or {}).get("alias") or "аноним"
            score = c.get("score")
            score_str = f"{score:+d}" if isinstance(score, int) else "?"
            ts = _fmt_ts(c.get("timePublished"))
            text = _strip_tags(c.get("message"))
            text = re.sub(r"\s+", " ", text).strip()
            indent = "  " * depth
            status = c.get("status")
            if status and status != "published":
                text = text or f"[{status}]"
            out.append(f"{indent}- **{author}** ({score_str}, {ts}): {text}")
            walk(c.get("id"), depth + 1)

    walk(None, 0)
    if not out:
        return None

    header = [
        "---",
        f"article_id: {article.id}",
        f"article_url: {article.url}",
        f"comments_count: {_yaml_scalar(article.comments_count)}",
        f"parsed_at: {_yaml_raw(article.parsed_at)}",
        "---",
        "",
    ]
    return "\n".join(header) + "\n".join(out) + "\n"
