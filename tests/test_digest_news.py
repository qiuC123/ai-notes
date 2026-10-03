from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_notes import digest


def news(*, url="https://publisher.example/announcements/model-access", day="2026-10-03", **changes):
    record = dict(url=url, title="Model access opens", category="模型与运行工具",
                  summary="The publisher opened model access to additional users.",
                  reason="Eligible users can now try the documented capability.",
                  source_urls=[url], published_at=day, kind="news", evidence_status="verified",
                  evidence_urls=[url], change_note="", discovered_at=day + "T12:00:00+08:00",
                  verified_at=day + "T13:00:00+08:00", verification_level="documented",
                  event=dict(id="model-access-" + day, url=url, occurred_at=day + "T10:00:00+08:00", type="news"))
    record.update(changes)
    return record


def batch(record, run_id="news"):
    return dict(schema_version="digest-batch.v2", run_id=run_id,
                collected_at=record.get("verified_at") or record["discovered_at"],
                sources=[dict(name="Publisher", url=record["url"], status="ok", detail="Read original announcement.")],
                candidates=[record])


def item(record):
    entry = {key: copy.deepcopy(record[key]) for key in (
        "url", "title", "category", "summary", "reason", "kind", "evidence_urls",
        "change_note", "verification_level", "verified_at")}
    if record.get("event"):
        entry["event"] = copy.deepcopy(record["event"])
    entry.update(featured=True, audience="Users newly eligible for model access",
                 usage_conditions="Check account eligibility in the publisher announcement.",
                 detail="Practical implications of the newly available capability.")
    return entry


def issue(record, *, period="2026-10-03", prepared="2026-10-04T09:00:00+08:00", ranking="daily"):
    return dict(schema_version="digest-issue.v2", ranking_type=ranking, period=period,
                title="Useful AI news", prepared_at=prepared,
                shortfall_reason="Only one qualified original announcement in this fixture.",
                verification_note="Original publisher announcement checked.", items=[item(record)])


class DigestNewsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock = patch.object(digest, "_now", return_value=datetime.fromisoformat("2026-10-04T09:01:00+08:00"))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_news_url_keeps_github_article_identity_and_existing_kinds(self):
        url = "http://www.github.com/Example/Tool/releases/tag/v2?utm_source=feed"
        self.assertEqual("https://github.com/example/tool/releases/tag/v2", digest.canonical_url(url, "news"))
        self.assertEqual("https://github.com/example/tool", digest.canonical_url(url, "update"))
        self.assertEqual("https://github.com/example/tool", digest.canonical_url(url, "project"))
        self.assertEqual(8, len(digest.CATEGORIES))

    def test_undated_discovery_is_reviewable_but_verified_news_requires_original_event(self):
        discovered = news(evidence_status="discovered", evidence_urls=[], verified_at=None, published_at=None)
        discovered.pop("event")
        digest.ingest(self.root, batch(discovered, "discovery"))
        candidates = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual("needs_evidence", candidates["candidates"][0]["selection_status"])
        invalid = news()
        invalid.pop("event")
        with self.assertRaisesRegex(digest.DigestError, "stable event"):
            digest.ingest(self.root, batch(invalid))
        invalid = news(evidence_urls=["https://publisher.example/"])
        with self.assertRaisesRegex(digest.DigestError, "original evidence_urls"):
            digest.ingest(self.root, batch(invalid))
        for changes in (dict(type="update"), dict(occurred_at="2026-10-03"),
                        dict(occurred_at="2026-10-03T14:00:00+08:00")):
            invalid = news()
            invalid["event"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(digest.DigestError):
                digest.ingest(self.root, batch(invalid))
        self.assertEqual(1, len(digest.candidates(self.root, ranking_type="daily", period="2026-10-03")["candidates"]))

    def test_news_archives_without_repository_reading_author_or_tested_deployment(self):
        record = news(category="博客、帖子与访谈")
        digest.ingest(self.root, batch(record))
        document = issue(record)
        result = digest.archive(self.root, document)
        article = Path(result["article_path"]).read_text(encoding="utf-8")
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual("news", manifest["items"][0]["event"]["type"])
        self.assertEqual(record["url"], manifest["items"][0]["canonical_url"])
        self.assertIn("新闻事件日期：2026-10-03", article)
        self.assertIn("影响人群：", article)
        self.assertIn("适用范围与行动：", article)
        self.assertIn("已核对原始新闻资料", article)
        self.assertNotIn("作者／受访者", article)
        self.assertNotIn("已本机实测", article)
        self.assertEqual("unchanged", digest.archive(self.root, document)["status"])

    def test_news_discovery_and_verification_are_one_card_and_reported_card_stays_blocked(self):
        record = news()
        discovered = news(evidence_status="discovered", evidence_urls=[], verified_at=None, published_at=None)
        discovered.pop("event")
        digest.ingest(self.root, batch(discovered, "discovered"))
        digest.ingest(self.root, batch(record, "verified"))
        rediscovered = copy.deepcopy(discovered)
        rediscovered["discovered_at"] = "2026-10-03T15:00:00+08:00"
        digest.ingest(self.root, batch(rediscovered, "rediscovered"))
        query = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual(1, len(query["candidates"]))
        events = query["candidates"][0]["event_candidates"]
        self.assertEqual(1, len(events))
        self.assertEqual(("verified", "available", 3),
                         (events[0]["evidence_status"], events[0]["selection_status"], events[0]["observation_count"]))
        self.assertEqual(record["event"], events[0]["event"])
        digest.archive(self.root, issue(record))
        query = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual([], query["candidates"])
        self.assertEqual(1, len(query["blocked_candidates"]))
        self.assertIn("same event", query["blocked_candidates"][0]["selection_block"])
        # Outside the original day, even an old undated discovery cannot revive
        # a previously reported news event when its verified record is filtered.
        later = digest.candidates(self.root, ranking_type="daily", period="2026-10-04")
        self.assertEqual([], later["candidates"])
        self.assertEqual(0, later["total_candidates"])

    def test_late_undated_news_can_be_reviewed_then_backfilled_but_not_archived_unverified(self):
        discovered = news(evidence_status="discovered", evidence_urls=[], verified_at=None, published_at=None,
                          discovered_at="2026-10-04T08:00:00+08:00")
        discovered.pop("event")
        digest.ingest(self.root, batch(discovered, "late-discovery"))
        query = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual(1, len(query["candidates"]))
        self.assertEqual("needs_evidence", query["candidates"][0]["selection_status"])
        unverified_issue = issue(discovered)
        unverified_issue["items"][0].update(verified_at="2026-10-04T08:30:00+08:00", evidence_urls=[discovered["url"]])
        with self.assertRaisesRegex(digest.DigestError, "stable event"):
            digest.archive(self.root, unverified_issue)
        verified = news(discovered_at=discovered["discovered_at"], verified_at="2026-10-04T08:30:00+08:00")
        digest.ingest(self.root, batch(verified, "late-verification"))
        query = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual("available", query["candidates"][0]["selection_status"])
        self.assertEqual(1, len(query["candidates"][0]["event_candidates"]))
        self.assertEqual("archived", digest.archive(self.root, issue(verified))["status"])
        # The exception is specific to a news discovery, not every late project.
        project = dict(discovered, kind="project")
        start, end, _ = digest.period_window("daily", "2026-10-03")
        self.assertFalse(digest._eligible(project, project["discovered_at"], start, end))

    def test_late_news_confirmed_outside_period_removes_undated_discovery_too(self):
        discovered = news(evidence_status="discovered", evidence_urls=[], verified_at=None, published_at=None,
                          discovered_at="2026-10-04T08:00:00+08:00")
        discovered.pop("event")
        digest.ingest(self.root, batch(discovered, "late-discovery"))
        self.assertEqual(1, len(digest.candidates(self.root, ranking_type="daily", period="2026-10-03")["candidates"]))
        verified = news(day="2026-10-02", discovered_at=discovered["discovered_at"], verified_at="2026-10-04T08:30:00+08:00")
        digest.ingest(self.root, batch(verified, "out-of-period-verification"))
        query = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual([], query["candidates"])
        with self.assertRaisesRegex(digest.DigestError, "Q12"):
            digest.archive(self.root, issue(verified))
        original_period = digest.candidates(self.root, ranking_type="daily", period="2026-10-02")
        self.assertEqual("available", original_period["candidates"][0]["selection_status"])
        self.assertEqual(1, len(original_period["candidates"][0]["event_candidates"]))

    def test_undated_news_discovery_cannot_revive_an_event_reported_as_update(self):
        url = "https://github.com/example/model/releases/tag/v2"
        record = news(url="https://github.com/example/model", kind="update", change_note="Opens access.")
        record["event"].update(url=url, type="update")
        record["evidence_urls"] = [url]
        digest.ingest(self.root, batch(record, "update"))
        digest.archive(self.root, issue(record))
        discovered = news(url=url, evidence_status="discovered", evidence_urls=[], verified_at=None, published_at=None)
        discovered.pop("event")
        digest.ingest(self.root, batch(discovered, "news-discovery"))
        query = digest.candidates(self.root, ranking_type="daily", period="2026-10-03")
        self.assertEqual([], query["candidates"])
        self.assertEqual(2, len(query["blocked_candidates"]))
        self.assertTrue(all("same event" in row["selection_block"] for row in query["blocked_candidates"]))

    def test_news_real_event_date_cannot_be_refreshed_by_discovery_or_verification(self):
        record = news(day="2026-10-01", discovered_at="2026-10-03T12:00:00+08:00", verified_at="2026-10-03T13:00:00+08:00")
        digest.ingest(self.root, batch(record))
        self.assertEqual([], digest.candidates(self.root, ranking_type="daily", period="2026-10-03")["candidates"])
        with self.assertRaisesRegex(digest.DigestError, "Q12"):
            digest.archive(self.root, issue(record))
        self.assertEqual("model-access-2026-10-01", digest.candidates(self.root, ranking_type="daily", period="2026-10-01")["candidates"][0]["event"]["id"])

    def test_news_q12_backfill_preserves_actual_collection_and_original_event_day(self):
        record = news(discovered_at="2026-10-04T08:00:00+08:00", verified_at="2026-10-04T08:30:00+08:00")
        digest.ingest(self.root, batch(record))
        result = digest.archive(self.root, issue(record))
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        selected = manifest["items"][0]
        self.assertEqual("原期事件补采", selected["period_label"])
        self.assertEqual("2026-10-04T08:30:00+08:00", selected["collected_at"])
        self.assertEqual(["2026-10-03"], manifest["coverage"]["missing_collection_dates"])
        self.assertEqual("2026-10-04", manifest["supplemental_runs"][0]["collection_date"])
        start, end, _ = digest.period_window("daily", "2026-10-03")
        no_original = copy.deepcopy(record)
        no_original["evidence_urls"] = ["https://publisher.example/"]
        self.assertFalse(digest._eligible(no_original, record["discovered_at"], start, end))

    def test_original_event_dedup_is_same_level_and_cannot_be_bypassed_by_kind_or_url(self):
        event_url = "https://github.com/example/model/releases/tag/v2"
        for first_kind, second_kind in (("update", "news"), ("news", "update"), ("news", "news")):
            with self.subTest(first_kind=first_kind, second_kind=second_kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                first = news(url=event_url, day="2026-10-01", kind=first_kind, change_note="Opens documented access.")
                first["event"]["type"] = first_kind
                if first_kind == "update":
                    first["url"] = "https://github.com/example/model"
                digest.ingest(root, batch(first, "first"))
                with patch.object(digest, "_now", return_value=datetime.fromisoformat("2026-10-02T09:01:00+08:00")):
                    digest.archive(root, issue(first, period="2026-10-01", prepared="2026-10-02T09:00:00+08:00"))
                second = news(url=event_url, kind=second_kind, change_note="Reworded announcement.")
                second["event"].update(id="renamed-event", url=event_url + "?utm_source=other", type=second_kind)
                second["evidence_urls"] = [event_url]
                second["url"] = "https://github.com/example/model" if second_kind == "update" else "https://publisher.example/retelling"
                digest.ingest(root, batch(second, "second"))
                query = digest.candidates(root, ranking_type="daily", period="2026-10-03")
                self.assertEqual([], query["candidates"])
                self.assertTrue(any("same event" in row["selection_block"] for row in query["blocked_candidates"]))
                with self.assertRaisesRegex(digest.DigestError, "same event"):
                    digest.archive(root, issue(second))
                # Cross-level reuse stays valid, with a complete natural week.
                weekly = issue(second, ranking="weekly", period="2026-09-28", prepared="2026-10-04T09:00:00+08:00")
                self.assertIn("草稿", digest.render(root, weekly))

    def test_same_issue_cannot_repeat_original_event_under_news_and_update(self):
        url = "https://github.com/example/model/releases/tag/v2"
        announcement = news(url=url)
        update = news(url="https://github.com/example/model", kind="update", change_note="Opens model access.")
        update["event"].update(url=url, type="update")
        update["evidence_urls"] = [url]
        document = issue(announcement)
        document["items"].append(item(update))
        with self.assertRaisesRegex(digest.DigestError, "same original event"):
            digest._validate_issue(document)


if __name__ == "__main__":
    unittest.main()
