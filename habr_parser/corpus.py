"""Файловый слой корпуса: пути, имена, сводный индекс, состояние (SPEC §5, §11)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .render import Article

# Транслитерация кириллицы для slug из заголовка (в URL Хабра текстового
# хвоста нет — только числовой id, поэтому slug строим из title).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(title: str, *, max_len: int = 60) -> str:
    text = (title or "").lower()
    out = []
    for ch in text:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "article"


class Corpus:
    def __init__(self, root: str | Path, me: str):
        self.root = Path(root)
        self.me = (me or "").lower()
        self.mine = self.root / "mine"
        self.reference = self.root / "reference"
        self.mine.mkdir(parents=True, exist_ok=True)
        self.reference.mkdir(parents=True, exist_ok=True)

    def _subdir(self, article: Article) -> Path:
        return self.mine if article.author.lower() == self.me else self.reference

    def basename(self, article: Article, existing: set[str]) -> str:
        date = (article.published or "")[:10] or "0000-00-00"
        base = f"{date}-{slugify(article.title)}"
        if base in existing:  # коллизия → добавляем id (SPEC §5)
            base = f"{base}-{article.id}"
        return base

    def write_article(self, article: Article, article_md: str,
                      comments_md: str | None, existing: set[str]) -> Path:
        subdir = self._subdir(article)
        base = self.basename(article, existing)
        existing.add(base)
        path = subdir / f"{base}.md"
        path.write_text(article_md, encoding="utf-8")
        if comments_md:
            (subdir / f"{base}.comments.md").write_text(comments_md, encoding="utf-8")
        return path

    # ---- сводный индекс -----------------------------------------------------

    def _collect_meta(self) -> list[dict]:
        """Считывает frontmatter всех статей корпуса (для reindex)."""
        metas = []
        for md in sorted(self.root.glob("*/*.md")):
            if md.name.endswith(".comments.md"):
                continue
            metas.append(_read_frontmatter(md, self.root))
        return metas

    def write_indexes(self, metas: list[dict] | None = None) -> None:
        if metas is None:
            metas = self._collect_meta()
        metas.sort(key=lambda m: m.get("published") or "", reverse=True)
        self._write_index_md(metas)
        self._write_jsonl(metas)

    def _write_index_md(self, metas: list[dict]) -> None:
        lines = [
            "# INDEX — корпус статей",
            "",
            f"Всего статей: {len(metas)}. Обновлено: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "| Дата | Заголовок | Автор | Хабы | Рейтинг | Закладки | Ссылка |",
            "|------|-----------|-------|------|--------:|---------:|--------|",
        ]
        for m in metas:
            date = (m.get("published") or "")[:10]
            who = "свой" if m.get("author", "").lower() == self.me else m.get("author", "")
            hubs = ", ".join(m.get("hubs") or [])
            title = (m.get("title") or "").replace("|", "\\|")
            lines.append(
                f"| {date} | {title} | {who} | {hubs} | "
                f"{m.get('rating')} | {m.get('bookmarks')} | [ссылка]({m.get('url')}) |"
            )
        (self.root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_jsonl(self, metas: list[dict]) -> None:
        with (self.root / "corpus.jsonl").open("w", encoding="utf-8") as f:
            for m in metas:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")


# ---- state.json / errors.log ---------------------------------------------

class RunState:
    def __init__(self, root: str | Path):
        self.state_path = Path(root) / "state.json"
        self.errors_path = Path(root) / "errors.log"
        self.data = {"done": [], "errors": []}
        if self.state_path.exists():
            try:
                self.data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

    def is_done(self, article_id: str) -> bool:
        return article_id in self.data.get("done", [])

    def mark_done(self, article_id: str) -> None:
        if article_id not in self.data["done"]:
            self.data["done"].append(article_id)
        self._flush()

    def log_error(self, url: str, kind: str, message: str) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        self.data.setdefault("errors", []).append(
            {"url": url, "kind": kind, "message": message, "at": stamp}
        )
        with self.errors_path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp}\t{kind}\t{url}\t{message}\n")
        self._flush()

    def _flush(self) -> None:
        self.state_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ---- чтение frontmatter (минимальный парсер для reindex) ------------------

_SCALAR_KEYS = {"id", "url", "title", "author", "published", "rating",
                "views", "comments_count", "bookmarks", "word_count", "type"}


def _read_frontmatter(path: Path, root: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict = {"_path": str(path.relative_to(root))}
    if not text.startswith("---"):
        return meta
    block = text.split("---", 2)[1]
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key in ("hubs", "tags"):
            meta[key] = _parse_flow_list(val)
        elif val.startswith('"'):
            meta[key] = val.strip('"').replace('\\"', '"').replace("\\\\", "\\")
        elif val in ("null", ""):
            meta[key] = None
        elif val.lstrip("-").isdigit():
            meta[key] = int(val)
        else:
            meta[key] = val
    return meta


def _parse_flow_list(val: str) -> list[str]:
    val = val.strip()
    if val in ("[]", ""):
        return []
    inner = val.strip("[]")
    return [item.strip().strip('"') for item in inner.split(",") if item.strip()]
