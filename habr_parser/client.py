"""Клиент неофициального JSON API Хабра (тот, что использует мобильное приложение).

Инкапсулирует все обращения к habr.com: вежливость (задержка между запросами,
внятный User-Agent), ретраи с экспоненциальным бэкоффом на сетевых ошибках,
429 и 5xx. Смена/поломка API затрагивает только этот слой (SPEC §4).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote

API_BASE = "https://habr.com/kek/v2/articles"


class ArticleUnavailable(Exception):
    """Статья недоступна: 404, только для авторизованных и т.п. (SPEC §13)."""


@dataclass
class HabrClient:
    user_agent: str
    delay_seconds: float = 2.5
    max_retries: int = 4
    timeout: float = 30.0

    _last_request_at: float = 0.0

    def _throttle(self) -> None:
        """Гарантирует паузу delay_seconds между последовательными запросами."""
        if self._last_request_at:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.delay_seconds - elapsed
            if wait > 0:
                time.sleep(wait)

    def _get_json(self, url: str) -> dict:
        self._throttle()
        backoff = self.delay_seconds
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                self._last_request_at = time.monotonic()
                return json.loads(data)
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
                if exc.code in (403, 404, 451):
                    # Недоступно без авторизации / удалено / юридически закрыто —
                    # ретраить бессмысленно.
                    raise ArticleUnavailable(f"HTTP {exc.code} для {url}") from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_exc = exc
                else:
                    raise
            except (urllib.error.URLError, TimeoutError) as exc:
                self._last_request_at = time.monotonic()
                last_exc = exc

            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff *= 2  # экспоненциальный бэкофф

        raise last_exc if last_exc else RuntimeError(f"Не удалось получить {url}")

    def get_article(self, article_id: str | int) -> dict:
        """Полные данные статьи: мета, метрики, тело как HTML-фрагмент."""
        return self._get_json(f"{API_BASE}/{article_id}/?fl=ru&hl=ru")

    def get_comments(self, article_id: str | int) -> dict:
        """Дерево комментариев статьи."""
        return self._get_json(f"{API_BASE}/{article_id}/comments/?fl=ru&hl=ru")

    def get_author_ids(self, username: str) -> list[str]:
        """Все id публикаций автора: проходим постранично по listing-эндпоинту.

        Ответ отдаёт только id и лёгкую мету (`publicationRefs`) — тело статьи
        приходит уже из get_article, поэтому здесь берём лишь список id.
        """
        seen: set[str] = set()
        ids: list[str] = []
        page = 1
        while True:
            data = self._get_json(
                f"{API_BASE}/?user={quote(username)}&page={page}&fl=ru&hl=ru"
            )
            for raw in data.get("publicationIds") or []:
                aid = str(raw)
                if aid not in seen:  # страховка от пересечений между страницами
                    seen.add(aid)
                    ids.append(aid)
            pages = data.get("pagesCount") or 1
            if page >= pages:
                break
            page += 1
        return ids
