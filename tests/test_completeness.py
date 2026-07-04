"""Тесты проверки полноты корпуса (completeness check).

Проверяют `check_completeness` и `_is_blank` из corpus.py: у каждой статьи
обязаны быть непустыми title, дата публикации, хабы, теги, рейтинг, закладки
и тело. Числовой 0 (в т.ч. рейтинг 0/отрицательный) — валидное значение, а не
пропуск. Сеть и реальный корпус не трогаем — статьи пишем во временную папку.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from habr_parser.corpus import REQUIRED_FIELDS, check_completeness, _is_blank

FULL = (
    "---\n"
    'title: "Полная статья"\n'
    "published: 2026-01-02T00:00:00+00:00\n"
    'hubs: ["ai"]\n'
    'tags: ["nlp"]\n'
    "rating: 7\n"
    "bookmarks: 12\n"
    "---\n\n"
    "Тело статьи.\n"
)


class IsBlankTests(unittest.TestCase):
    def test_zero_and_negative_are_filled(self):
        # Рейтинг 0 или отрицательный, 0 закладок — валидные значения.
        self.assertFalse(_is_blank(0))
        self.assertFalse(_is_blank(-5))

    def test_none_empty_string_empty_list_are_blank(self):
        self.assertTrue(_is_blank(None))
        self.assertTrue(_is_blank(""))
        self.assertTrue(_is_blank("   "))
        self.assertTrue(_is_blank([]))


class CheckCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "mine").mkdir()

    def _write(self, name: str, text: str) -> None:
        (self.root / "mine" / name).write_text(text, encoding="utf-8")

    def _missing(self, name: str) -> list[str]:
        reports = {r["path"]: r["missing"] for r in check_completeness(self.root)}
        return reports[f"mine/{name}"]

    def test_full_article_has_no_missing(self):
        self._write("full.md", FULL)
        self.assertEqual(self._missing("full.md"), [])

    def test_zero_metrics_are_valid(self):
        self._write("zero.md", FULL.replace("rating: 7", "rating: 0")
                    .replace("bookmarks: 12", "bookmarks: 0"))
        self.assertEqual(self._missing("zero.md"), [])

    def test_blank_fields_are_reported(self):
        bad = (
            "---\n"
            'title: ""\n'
            "published: 2026-01-01T00:00:00+00:00\n"
            'hubs: ["ai"]\n'
            "tags: []\n"
            "rating: null\n"
            "bookmarks: 5\n"
            "---\n\n"
        )  # пустой title, пустые tags, rating=null, пустое тело
        self._write("bad.md", bad)
        self.assertEqual(set(self._missing("bad.md")),
                         {"title", "tags", "rating", "body"})

    def test_comments_files_are_skipped(self):
        self._write("art.md", FULL)
        self._write("art.comments.md", "# комментарии\n\nтекст\n")
        paths = [r["path"] for r in check_completeness(self.root)]
        self.assertIn("mine/art.md", paths)
        self.assertNotIn("mine/art.comments.md", paths)

    def test_required_fields_are_the_spec_set(self):
        self.assertEqual(
            set(REQUIRED_FIELDS),
            {"title", "published", "hubs", "tags", "rating", "bookmarks"},
        )


if __name__ == "__main__":
    unittest.main()
