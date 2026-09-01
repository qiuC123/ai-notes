from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.collectors.parsers import (
    parse_aihot_hot_topics,
    parse_aihot_selected,
    parse_arxiv_atom,
    parse_atom,
    parse_huggingface_models,
    parse_rss,
)


FIXTURES = Path(__file__).parent / "fixtures"


class CollectorParserTests(unittest.TestCase):
    def test_parses_aihot_selected_json_and_prefers_original_link(self) -> None:
        payload = json.loads((FIXTURES / "aihot_selected.json").read_text(encoding="utf-8"))

        records = parse_aihot_selected(payload)

        self.assertEqual(1, len(records))
        self.assertEqual("OpenAI releases Responses API update", records[0].title)
        self.assertEqual("https://openai.com/index/responses-api-update", records[0].url)
        self.assertEqual("2026-08-27T01:02:03Z", records[0].published_at)

    def test_parses_aihot_hot_topics_json(self) -> None:
        payload = json.loads((FIXTURES / "aihot_hot_topics.json").read_text(encoding="utf-8"))

        records = parse_aihot_hot_topics(payload)

        self.assertEqual(1, len(records))
        self.assertEqual("MCP server release", records[0].title)
        self.assertIn("4 sources", records[0].summary)
        self.assertEqual("https://example.test/mcp-release", records[0].url)

    def test_parses_rss(self) -> None:
        records = parse_rss((FIXTURES / "news.rss.xml").read_text(encoding="utf-8"))

        self.assertEqual(1, len(records))
        self.assertEqual("Official feature announcement", records[0].title)
        self.assertEqual("https://example.test/news/feature", records[0].url)
        self.assertEqual(["product"], records[0].categories)

    def test_parses_github_release_atom(self) -> None:
        records = parse_atom((FIXTURES / "release.atom.xml").read_text(encoding="utf-8"))

        self.assertEqual(1, len(records))
        self.assertEqual("v1.2.3", records[0].title)
        self.assertEqual("https://github.com/example/project/releases/tag/v1.2.3", records[0].url)
        self.assertEqual("Example Org", records[0].owner)

    def test_parses_huggingface_model_json(self) -> None:
        payload = json.loads((FIXTURES / "huggingface_models.json").read_text(encoding="utf-8"))

        records = parse_huggingface_models(payload)

        self.assertEqual(1, len(records))
        self.assertEqual("acme/fast-vision", records[0].title)
        self.assertEqual("https://huggingface.co/acme/fast-vision", records[0].url)
        self.assertIn("image-classification", records[0].categories)

    def test_parses_arxiv_atom(self) -> None:
        records = parse_arxiv_atom((FIXTURES / "arxiv.atom.xml").read_text(encoding="utf-8"))

        self.assertEqual(1, len(records))
        self.assertEqual("A Useful Agent Paper", records[0].title)
        self.assertEqual("http://arxiv.org/abs/2608.12345", records[0].url)
        self.assertEqual("2026-08-26T12:00:00Z", records[0].published_at)


if __name__ == "__main__":
    unittest.main()
