from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from test_digest_news import batch, issue, news
from ai_notes import digest


def date_news(day="2026-09-29", *, url="https://publisher.example/announcements/date-only", observed="2026-10-04T08:30:00+08:00"):
    record = news(url=url, day=day, discovered_at=observed, verified_at=observed)
    record["event"] = dict(id="announcement-" + day, url=url, type="news",
                           occurred_on=day, date_precision="date", timezone="unknown")
    return record


class NewsDatePrecisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        clock = patch.object(digest, "_now", return_value=datetime.fromisoformat("2026-10-05T09:01:00+08:00"))
        clock.start()
        self.addCleanup(clock.stop)

    def test_timestamp_compatibility_and_date_only_bounds_keep_original_precision(self):
        event = news()["event"]
        point = datetime.fromisoformat(event["occurred_at"]).astimezone(timezone.utc)
        self.assertEqual((point, point), digest._event_bounds(event))
        utc = {**event, "occurred_at": point.isoformat()}
        self.assertEqual(digest._event_time_key(event), digest._event_time_key(utc))
        date_event = date_news()["event"]
        original = copy.deepcopy(date_event)
        bounds = digest._event_bounds(date_event)
        self.assertEqual(("2026-09-28T18:00:00+08:00", "2026-09-30T19:59:59.999999+08:00"),
                         tuple(moment.astimezone(digest.BEIJING).isoformat() for moment in bounds))
        self.assertEqual(["date", "2026-09-29", "date", "unknown"],
                         json.loads(json.dumps(digest._event_time_key(date_event))))
        self.assertEqual(original, date_event)
        self.assertNotIn("occurred_at", date_event)

    def test_date_only_requires_explicit_news_precision_and_rejects_future_or_mixed_dates(self):
        digest.ingest(self.root, batch(date_news()))
        for change in ({"date_precision": "timestamp"}, {"timezone": "+08:00"},
                       {"occurred_at": None}, {"occurred_at": "2026-09-29T00:00:00Z"},
                       {"occurred_on": "2026-09-29T00:00:00Z"}, {"occurred_on": "2027-01-01"}):
            record = date_news()
            record["event"].update(change)
            with self.subTest(change=change), self.assertRaises(digest.DigestError):
                digest.ingest(self.root, batch(record, "invalid"))
        for missing in ("date_precision", "timezone", "occurred_on"):
            record = date_news()
            record["event"].pop(missing)
            with self.subTest(missing=missing), self.assertRaises(digest.DigestError):
                digest.ingest(self.root, batch(record, "missing"))
        record = date_news()
        record.update(kind="update", change_note="An update.")
        record["event"]["type"] = "update"
        with self.assertRaisesRegex(digest.DigestError, "date-only events require news"):
            digest.ingest(self.root, batch(record, "nonnews"))

    def test_whole_unknown_timezone_range_must_fit_beijing_period(self):
        cases = [
            ("2026-09-29", "monthly", "2026-09", True),
            ("2026-09-30", "monthly", "2026-09", False),
            ("2026-10-01", "monthly", "2026-10", False),
            ("2026-09-30", "weekly", "2026-09-28", True),
            ("2026-09-28", "weekly", "2026-09-28", False),
            ("2026-10-04", "weekly", "2026-09-28", False),
            ("2026-09-29", "daily", "2026-09-28", False),
            ("2026-09-29", "daily", "2026-09-29", False),
            ("2026-09-29", "daily", "2026-09-30", False),
        ]
        for day, ranking, period, expected in cases:
            with self.subTest(day=day, ranking=ranking, period=period):
                record = date_news(day)
                start, end, _ = digest.period_window(ranking, period)
                self.assertEqual(expected, digest._event_in_period(record["event"], start, end))
                self.assertEqual(expected, digest._eligible(record, record["discovered_at"], start, end))

    def test_month_internal_backfill_requires_original_evidence_and_does_not_invent_time(self):
        record = date_news()
        digest.ingest(self.root, batch(record))
        found = digest.candidates(self.root, ranking_type="monthly", period="2026-09")["candidates"]
        self.assertEqual(1, len(found))
        self.assertEqual("available", found[0]["selection_status"])
        self.assertEqual(record["event"], found[0]["event"])
        self.assertEqual("原期事件补采", found[0]["period_label"])
        self.assertEqual(record["verified_at"], found[0]["last_verified_at"])
        start, end, _ = digest.period_window("monthly", "2026-09")
        no_original = dict(record, evidence_urls=["https://publisher.example/rss.xml"])
        self.assertFalse(digest._eligible(no_original, record["discovered_at"], start, end))
        with self.assertRaisesRegex(digest.DigestError, "original evidence_urls"):
            digest.ingest(self.root, batch(no_original, "rss-only"))
        publication_only = copy.deepcopy(record)
        publication_only.pop("event")
        with self.assertRaisesRegex(digest.DigestError, "stable event"):
            digest.ingest(self.root, batch(publication_only, "publication-is-not-event"))

    def test_weekly_archive_renders_date_only_and_preserves_immutable_replay(self):
        observed = "2026-10-05T08:30:00+08:00"
        record = date_news("2026-09-30", observed=observed)
        digest.ingest(self.root, batch(record))
        document = issue(record, period="2026-09-28", prepared="2026-10-05T09:00:00+08:00", ranking="weekly")
        result = digest.archive(self.root, document)
        article = Path(result["article_path"]).read_text(encoding="utf-8")
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertIn("新闻事件日期：2026-09-30（原文仅日期，时区未知；未确认具体时刻）", article)
        self.assertNotIn("2026-09-30T00:00", article)
        self.assertEqual(record["event"], manifest["items"][0]["event"])
        self.assertNotIn("occurred_at", manifest["items"][0]["event"])
        self.assertEqual(observed, manifest["items"][0]["verified_at"])
        self.assertEqual("unchanged", digest.archive(self.root, document)["status"])
        changed = copy.deepcopy(document)
        changed["items"][0]["event"]["occurred_on"] = "2026-10-01"
        with self.assertRaisesRegex(digest.DigestError, "different content"):
            digest.archive(self.root, changed)
        self.assertEqual([], digest.candidates(self.root, ranking_type="weekly", period="2026-09-28")["candidates"])

    def test_uncertain_month_week_and_day_boundaries_cannot_be_backfilled(self):
        cases = [("2026-09-30", "monthly", "2026-09"),
                 ("2026-09-28", "weekly", "2026-09-28"),
                 ("2026-10-04", "weekly", "2026-09-28"),
                 ("2026-09-29", "daily", "2026-09-29")]
        for index, (day, ranking, period) in enumerate(cases):
            with self.subTest(day=day, ranking=ranking):
                record = date_news(day, url=f"https://publisher.example/announcements/boundary-{index}")
                digest.ingest(self.root, batch(record, f"boundary-{index}"))
                document = issue(record, period=period, prepared="2026-10-04T09:00:00+08:00", ranking=ranking)
                document["items"][0]["retention_reason"] = "Potential long term implications."
                with self.assertRaisesRegex(digest.DigestError, "Q12.*uncertain date boundaries"):
                    digest.save_draft(self.root, document)
        self.assertEqual(0, digest.status(self.root)["issue_count"])

    def test_selection_cannot_upgrade_date_precision_without_matching_observation(self):
        record = date_news("2026-09-30")
        digest.ingest(self.root, batch(record))
        document = issue(record, period="2026-09-28", ranking="weekly")
        document["items"][0]["event"] = dict(id=record["event"]["id"], url=record["url"], type="news",
                                             occurred_at="2026-09-30T00:00:00Z")
        with self.assertRaisesRegex(digest.DigestError, "matching kind/event/time/level/evidence"):
            digest.save_draft(self.root, document)

    def test_dedup_rejects_uncertain_newness_and_same_event_across_precision_and_kind(self):
        previous = date_news("2026-09-15")
        # The ranges overlap: September 17 is not proven strictly newer than
        # every possible instant of the September 15 event.
        overlapping = date_news("2026-09-17")
        later = date_news("2026-09-18")
        overlapping["event"]["url"] += "#next"
        later["event"]["url"] += "#later"
        self.assertIn("newer", digest._dedup_error(overlapping, [previous]))
        self.assertIsNone(digest._dedup_error(later, [previous]))
        update = news(url="https://publisher.example/model", day="2026-09-18", kind="update", change_note="Renamed event.")
        update["event"].update(type="update", url=previous["event"]["url"])
        self.assertIn("same event", digest._dedup_error(update, [], {digest._event_url(previous["event"]["url"])}))


if __name__ == "__main__":
    unittest.main()
