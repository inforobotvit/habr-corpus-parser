"""Тесты извлечения полей из JSON API Хабра.

Проверяют `Article.from_api` и связанные хелперы (render.py), а также
конвертацию тела и подсчёт слов (convert.py). Сеть не трогаем — на входе
фиктивный ответ API, максимально близкий к реальной структуре.
"""

from __future__ import annotations

import json
import unittest

from habr_parser.convert import html_to_markdown, word_count
from habr_parser.render import (
    Article,
    _extract_modified,
    _map_type,
    _strip_tags,
)

PARSED_AT = "2026-07-04T12:00:00+03:00"


def make_payload(**overrides) -> dict:
    """Реалистичный ответ API одной статьи; поля переопределяются через kwargs."""
    data = {
        "id": 123456,
        "titleHtml": "Как я парсил &laquo;Хабр&raquo;",
        "timePublished": "2024-03-15T10:30:00+03:00",
        "author": {"alias": "vitalijturov"},
        "hubs": [
            {"alias": "python", "title": "Python"},
            {"alias": "data_mining", "title": "Data Mining"},
        ],
        "tags": [
            {"titleHtml": "парсинг"},
            {"titleHtml": "habr"},
            {"titleHtml": "nlp"},
        ],
        "postType": "article",
        "postLabels": [],
        "statistics": {
            "score": 42,
            "readingCount": 15000,
            "commentsCount": 37,
            "favoritesCount": 12,
        },
        "readingTime": 12,
        "complexity": "medium",
        "format": "tutorial",
        "textHtml": "<p>Привет, мир.</p>",
        "metadata": {
            "schemaJsonLd": json.dumps({"dateModified": "2024-03-16T09:00:00+03:00"})
        },
    }
    data.update(overrides)
    return data


def build(**overrides) -> Article:
    return Article.from_api(make_payload(**overrides), parsed_at=PARSED_AT)


class BasicFieldsTest(unittest.TestCase):
    def test_id_is_string(self):
        self.assertEqual(build().id, "123456")

    def test_url_built_from_id(self):
        self.assertEqual(build().url, "https://habr.com/ru/articles/123456/")

    def test_title_strips_tags_and_unescapes_entities(self):
        art = build(titleHtml="<b>Как</b> я парсил &laquo;Хабр&raquo;")
        self.assertEqual(art.title, "Как я парсил «Хабр»")

    def test_author_and_author_url(self):
        art = build()
        self.assertEqual(art.author, "vitalijturov")
        self.assertEqual(art.author_url, "https://habr.com/ru/users/vitalijturov/")

    def test_missing_author_yields_empty_author_url(self):
        art = build(author={})
        self.assertEqual(art.author, "")
        self.assertEqual(art.author_url, "")

    def test_published_passthrough(self):
        self.assertEqual(build().published, "2024-03-15T10:30:00+03:00")

    def test_source_is_api(self):
        self.assertEqual(build().source, "api")

    def test_parsed_at_passthrough(self):
        self.assertEqual(build().parsed_at, PARSED_AT)


class ClassificationTest(unittest.TestCase):
    def test_hubs_use_alias(self):
        self.assertEqual(build().hubs, ["python", "data_mining"])

    def test_hub_falls_back_to_title_when_no_alias(self):
        art = build(hubs=[{"title": "<i>Machine Learning</i>"}])
        self.assertEqual(art.hubs, ["Machine Learning"])

    def test_tags_extracted_and_empty_dropped(self):
        art = build(tags=[{"titleHtml": "python"}, {"titleHtml": ""}, {"title": "nlp"}])
        self.assertEqual(art.tags, ["python", "nlp"])

    def test_tag_title_fallback(self):
        art = build(tags=[{"title": "только-title"}])
        self.assertEqual(art.tags, ["только-title"])

    def test_type_article_default(self):
        self.assertEqual(build().type, "article")

    def test_type_news(self):
        self.assertEqual(build(postType="news").type, "news")

    def test_type_megapost(self):
        self.assertEqual(build(postType="megapost").type, "megapost")

    def test_format_passthrough(self):
        self.assertEqual(build().fmt, "tutorial")

    def test_complexity_passthrough(self):
        self.assertEqual(build().complexity, "medium")


class CompanyTest(unittest.TestCase):
    def test_company_none_for_non_corporate(self):
        self.assertIsNone(build().company)

    def test_company_from_corporate_hub(self):
        art = build(
            isCorporative=True,
            hubs=[
                {"alias": "python", "title": "Python", "type": "collective"},
                {"alias": "acme", "title": "Компания ACME", "type": "corporate"},
            ],
        )
        self.assertEqual(art.company, "Компания ACME")

    def test_company_none_when_flag_set_but_no_corporate_hub(self):
        art = build(isCorporative=True, hubs=[{"alias": "python", "title": "Python"}])
        self.assertIsNone(art.company)


class TranslationTest(unittest.TestCase):
    def test_not_translation_by_default(self):
        art = build()
        self.assertFalse(art.translation)
        self.assertIsNone(art.original_url)

    def test_translation_via_post_label(self):
        art = build(postLabels=[{"type": "translation"}])
        self.assertTrue(art.translation)

    def test_translation_via_linked_post(self):
        art = build(linkedPostTranslation={"id": 999})
        self.assertTrue(art.translation)

    def test_original_url_from_translation_data(self):
        art = build(
            postLabels=[{"type": "translation"}],
            translationData={"originalUrl": "https://example.com/original"},
        )
        self.assertEqual(art.original_url, "https://example.com/original")

    def test_original_url_falls_back_to_text_field(self):
        art = build(translationData={"originalUrlText": "https://example.com/orig-text"})
        self.assertEqual(art.original_url, "https://example.com/orig-text")


class MetricsTest(unittest.TestCase):
    def test_all_metrics(self):
        art = build()
        self.assertEqual(art.rating, 42)
        self.assertEqual(art.views, 15000)
        self.assertEqual(art.comments_count, 37)
        self.assertEqual(art.bookmarks, 12)

    def test_reading_time(self):
        self.assertEqual(build().reading_time_min, 12)

    def test_missing_statistics_yield_none(self):
        art = build(statistics={})
        self.assertIsNone(art.rating)
        self.assertIsNone(art.views)
        self.assertIsNone(art.comments_count)
        self.assertIsNone(art.bookmarks)


class ModifiedTest(unittest.TestCase):
    def test_modified_from_json_ld(self):
        self.assertEqual(build().modified, "2024-03-16T09:00:00+03:00")

    def test_modified_none_without_metadata(self):
        self.assertIsNone(build(metadata=None).modified)

    def test_modified_none_on_broken_json(self):
        art = build(metadata={"schemaJsonLd": "{not valid json"})
        self.assertIsNone(art.modified)


class BodyTest(unittest.TestCase):
    def test_body_converted_to_markdown(self):
        art = build(textHtml="<h2>Заголовок</h2><p>Абзац текста.</p>")
        self.assertIn("## Заголовок", art.body_md)
        self.assertIn("Абзац текста.", art.body_md)

    def test_word_count_matches_body(self):
        art = build(textHtml="<p>раз два три четыре пять</p>")
        self.assertEqual(art.word_count, word_count(art.body_md))
        self.assertEqual(art.word_count, 5)

    def test_empty_body(self):
        art = build(textHtml="")
        self.assertEqual(art.body_md, "")
        self.assertEqual(art.word_count, 0)


class HelperTest(unittest.TestCase):
    def test_strip_tags_none(self):
        self.assertEqual(_strip_tags(None), "")

    def test_strip_tags_unescape(self):
        self.assertEqual(_strip_tags("<b>a</b> &amp; b"), "a & b")

    def test_map_type(self):
        self.assertEqual(_map_type("news"), "news")
        self.assertEqual(_map_type("megapost"), "megapost")
        self.assertEqual(_map_type("MEGA_POST"), "megapost")
        self.assertEqual(_map_type("article"), "article")
        self.assertEqual(_map_type(None), "article")

    def test_extract_modified_direct(self):
        raw = json.dumps({"dateModified": "2020-01-01T00:00:00+03:00"})
        self.assertEqual(_extract_modified({"schemaJsonLd": raw}), "2020-01-01T00:00:00+03:00")
        self.assertIsNone(_extract_modified(None))
        self.assertIsNone(_extract_modified({}))


class ConvertTest(unittest.TestCase):
    def test_code_block_keeps_language(self):
        html = '<pre><code class="python">print("hi")</code></pre>'
        md = html_to_markdown(html)
        self.assertIn("```python", md)
        self.assertIn('print("hi")', md)

    def test_code_block_ignores_hljs_class(self):
        html = '<pre><code class="hljs bash">ls -la</code></pre>'
        md = html_to_markdown(html)
        self.assertIn("```bash", md)

    def test_junk_block_stripped(self):
        html = '<p>оставить</p><div class="code-explainer">реклама</div>'
        md = html_to_markdown(html)
        self.assertIn("оставить", md)
        self.assertNotIn("реклама", md)

    def test_blank_lines_collapsed(self):
        html = "<p>a</p><p>b</p><p>c</p>"
        md = html_to_markdown(html)
        self.assertNotIn("\n\n\n", md)

    def test_headings_and_lists_preserved(self):
        html = "<h1>T</h1><ul><li>один</li><li>два</li></ul>"
        md = html_to_markdown(html)
        self.assertIn("# T", md)
        self.assertIn("- один", md)
        self.assertIn("- два", md)

    def test_word_count_unicode(self):
        self.assertEqual(word_count("раз два три"), 3)
        self.assertEqual(word_count(""), 0)


if __name__ == "__main__":
    unittest.main()
