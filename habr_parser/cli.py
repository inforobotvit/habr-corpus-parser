"""CLI: оркестрация прогона (SPEC §9, §10).

Команды первой версии:
  urls <file>       — распарсить статьи по списку URL (таблица urls.md)
  author <username> — скачать все статьи автора через listing API
  reindex           — пересобрать INDEX.md и corpus.jsonl из имеющихся файлов
  validate          — проверить полноту корпуса (обязательные поля не пусты)
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .client import ArticleUnavailable, HabrClient
from .corpus import REQUIRED_FIELDS, Corpus, RunState, check_completeness
from .render import Article, render_article, render_comments

MSK = timezone(timedelta(hours=3))

_DEFAULTS = {
    "me": "VitTurov",
    "output_dir": "corpus",
    "delay_seconds": 2.5,
    "user_agent": "habr-corpus-parser (contact: inforobot.vit@gmail.com)",
    "download_images": False,
    "include_comments": True,
}


def load_config(path: str = "habr.yaml") -> dict:
    """Минимальный ридер плоского YAML-конфига (без PyYAML)."""
    cfg = dict(_DEFAULTS)
    p = Path(path)
    if not p.exists():
        return cfg
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip().strip('"')
        if key not in cfg:
            continue
        if val.lower() in ("true", "false"):
            cfg[key] = val.lower() == "true"
        elif re.fullmatch(r"\d+(\.\d+)?", val):
            cfg[key] = float(val) if "." in val else int(val)
        else:
            cfg[key] = val
    return cfg


_ID_RE = re.compile(r"habr\.com/[^\s)]*?/(\d+)/?")


def extract_ids(text: str) -> list[str]:
    """Достаёт id статей из habr-URL в тексте (в порядке появления, без дублей)."""
    seen: list[str] = []
    for m in _ID_RE.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _now_iso() -> str:
    return datetime.now(MSK).isoformat(timespec="seconds")


def run_urls(urls_file: str, cfg: dict, force: bool = False) -> int:
    ids = extract_ids(Path(urls_file).read_text(encoding="utf-8"))
    if not ids:
        print(f"В {urls_file} не найдено ссылок на статьи habr.com", file=sys.stderr)
        return 1
    return _process_ids(ids, cfg, force=force)


def run_author(username: str, cfg: dict, force: bool = False) -> int:
    client = HabrClient(user_agent=cfg["user_agent"], delay_seconds=cfg["delay_seconds"])
    print(f"Получаю список статей автора {username}…")
    try:
        ids = client.get_author_ids(username)
    except ArticleUnavailable as exc:
        print(f"Автор {username} недоступен — {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — сбой листинга не должен падать трейсбеком
        print(f"Не удалось получить список статей {username}: {exc}", file=sys.stderr)
        return 1
    if not ids:
        print(f"У автора {username} не найдено публичных статей", file=sys.stderr)
        return 1
    print(f"Найдено статей у {username}: {len(ids)}")
    # Тот же клиент передаём дальше — сохраняется троттлинг между листингом и статьями.
    return _process_ids(ids, cfg, force=force, client=client)


def _process_ids(ids: list[str], cfg: dict, *, force: bool = False,
                 client: HabrClient | None = None) -> int:
    client = client or HabrClient(
        user_agent=cfg["user_agent"], delay_seconds=cfg["delay_seconds"]
    )
    corpus = Corpus(cfg["output_dir"], cfg["me"])
    state = RunState(cfg["output_dir"])
    existing: set[str] = set()

    downloaded = skipped = errors = 0
    metas: list[dict] = []

    print(f"К обработке: {len(ids)} статей → {cfg['output_dir']}/\n")
    for i, aid in enumerate(ids, 1):
        url = f"https://habr.com/ru/articles/{aid}/"
        if not force and state.is_done(aid):
            print(f"[{i}/{len(ids)}] {aid}: уже обработана, пропуск")
            skipped += 1
            continue
        try:
            data = client.get_article(aid)
            article = Article.from_api(data, parsed_at=_now_iso())

            comments_md = None
            if cfg["include_comments"] and article.comments_count:
                try:
                    comments_md = render_comments(article, client.get_comments(aid))
                except Exception as exc:  # noqa: BLE001 — комментарии опциональны
                    state.log_error(url, "comments", str(exc))

            path = corpus.write_article(
                article, render_article(article), comments_md, existing
            )
            state.mark_done(aid)
            downloaded += 1
            metas.append(_meta_for_index(article))
            print(f"[{i}/{len(ids)}] {aid}: «{article.title[:60]}» "
                  f"→ {path.relative_to(corpus.root.parent)} "
                  f"({article.word_count} слов, ★{article.rating}, ☆{article.bookmarks})")
        except ArticleUnavailable as exc:
            errors += 1
            state.log_error(url, "unavailable", str(exc))
            print(f"[{i}/{len(ids)}] {aid}: недоступна — {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — сбой одной статьи не роняет прогон
            errors += 1
            state.log_error(url, type(exc).__name__, str(exc))
            print(f"[{i}/{len(ids)}] {aid}: ошибка — {exc}", file=sys.stderr)

    # Индекс собираем из всего корпуса, а не только из этого прогона.
    corpus.write_indexes()
    print(f"\nГотово. Скачано: {downloaded}, пропущено: {skipped}, ошибок: {errors}.")
    if errors:
        print(f"Проблемные URL — см. {corpus.root / 'errors.log'}")
    return 0


def _meta_for_index(a: Article) -> dict:
    return {
        "id": a.id, "url": a.url, "title": a.title, "author": a.author,
        "published": a.published, "hubs": a.hubs, "tags": a.tags,
        "rating": a.rating, "views": a.views, "bookmarks": a.bookmarks,
        "comments_count": a.comments_count, "word_count": a.word_count,
    }


def run_validate(cfg: dict) -> int:
    """Проверка полноты: у каждой статьи должны быть непустыми title, дата
    публикации, хабы, теги, рейтинг, закладки и тело. Пропуски логируются явно
    в corpus/validation.log (SPEC — completeness check)."""
    root = Path(cfg["output_dir"])
    reports = check_completeness(root)
    if not reports:
        print(f"В {root}/ не найдено статей для проверки")
        return 0

    incomplete = [r for r in reports if r["missing"]]
    print(f"Обязательные поля: {', '.join(REQUIRED_FIELDS)}, body")
    print(f"Проверено статей: {len(reports)}\n")

    for r in reports:
        if r["missing"]:
            print(f"❌ {r['path']} — ПУСТО: {', '.join(r['missing'])}")
        else:
            print(f"✅ {r['path']}")

    log_path = root / "validation.log"
    stamp = _now_iso()
    if incomplete:
        with log_path.open("a", encoding="utf-8") as f:
            for r in incomplete:
                for field in r["missing"]:
                    f.write(f"{stamp}\tmissing\t{r['path']}\t{field}\n")
        print(f"\nНеполных статей: {len(incomplete)} из {len(reports)}. "
              f"Пропуски записаны в {log_path}")
        return 1

    print(f"\nВсе {len(reports)} статей полны — пустых полей нет.")
    return 0


def run_reindex(cfg: dict) -> int:
    corpus = Corpus(cfg["output_dir"], cfg["me"])
    corpus.write_indexes()
    print(f"Индексы пересобраны: {corpus.root / 'INDEX.md'}, {corpus.root / 'corpus.jsonl'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="habr-parse", description="Парсер статей Хабра в markdown-корпус")
    parser.add_argument("--config", default="habr.yaml", help="путь к конфигу")
    sub = parser.add_subparsers(dest="command", required=True)

    p_urls = sub.add_parser("urls", help="распарсить статьи по списку URL")
    p_urls.add_argument("file", help="файл со ссылками (напр. urls.md)")
    p_urls.add_argument("--force", action="store_true", help="перекачать даже обработанные")

    p_author = sub.add_parser("author", help="скачать все статьи автора")
    p_author.add_argument("username", help="alias автора на Хабре (напр. VitTurov)")
    p_author.add_argument("--force", action="store_true", help="перекачать даже обработанные")

    sub.add_parser("reindex", help="пересобрать INDEX.md и corpus.jsonl")

    sub.add_parser("validate", help="проверить полноту корпуса (обязательные поля)")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.command == "urls":
        return run_urls(args.file, cfg, force=args.force)
    if args.command == "author":
        return run_author(args.username, cfg, force=args.force)
    if args.command == "reindex":
        return run_reindex(cfg)
    if args.command == "validate":
        return run_validate(cfg)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
