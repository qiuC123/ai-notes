from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.release_ledger import ReleaseLedger
from aihot.release_sources import ReleaseSource
from aihot.security_advisories import GitHubAdvisoryFetcher, parse_security_advisories


def source() -> ReleaseSource:
    return ReleaseSource(
        source_id="openai_codex",
        repository="openai/codex",
        category="ai_coding",
        feed_url="https://github.com/openai/codex/releases.atom",
        accepted_tag_patterns=(r"rust-v\d+\.\d+\.\d+",),
        package_aliases=("@openai/codex",),
    )


def advisory(ghsa: str, *, severity: str, location: str, package: str = "other") -> dict[str, object]:
    return {
        "ghsa_id": ghsa,
        "cve_id": "CVE-2026-0001",
        "html_url": f"https://github.com/advisories/{ghsa}",
        "summary": "A serious issue",
        "description": "Attackers can bypass a security boundary.",
        "type": "reviewed",
        "severity": severity,
        "source_code_location": location,
        "published_at": "2026-08-31T00:00:00Z",
        "updated_at": "2026-08-31T01:00:00Z",
        "withdrawn_at": None,
        "vulnerabilities": [
            {
                "package": {"ecosystem": "npm", "name": package},
                "vulnerable_version_range": "< 1.0.0",
                "first_patched_version": "1.0.0",
            }
        ],
    }


class SecurityAdvisoryTests(unittest.TestCase):
    def test_only_high_or_critical_reviewed_advisories_mapped_to_registry_are_collected(self) -> None:
        payload = [
            advisory("GHSA-aaaa-bbbb-cccc", severity="high", location="https://github.com/openai/codex"),
            advisory("GHSA-dddd-eeee-ffff", severity="medium", location="https://github.com/openai/codex"),
            advisory("GHSA-1111-2222-3333", severity="critical", location="https://github.com/other/project"),
        ]

        records = parse_security_advisories(
            payload,
            sources=[source()],
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/raw/2026-09-01/github_advisories.json",
        )

        self.assertEqual(1, len(records))
        self.assertEqual("github-advisory@GHSA-aaaa-bbbb-cccc", records[0].release_key)
        self.assertEqual("security", records[0].item_type)
        self.assertEqual("openai/codex", records[0].repository)
        self.assertEqual("high", records[0].metadata["severity"])

    def test_changed_security_advisory_content_is_reprocessed(self) -> None:
        original = parse_security_advisories(
            [advisory("GHSA-aaaa-bbbb-cccc", severity="high", location="https://github.com/openai/codex")],
            sources=[source()],
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/raw/2026-09-01/github_advisories.json",
        )[0]
        changed = replace(
            original,
            release_notes_html=original.release_notes_html + " Newly affected package.",
            release_notes_text=original.release_notes_text + " Newly affected package.",
        )
        with tempfile.TemporaryDirectory() as directory:
            ledger = ReleaseLedger(Path(directory) / "ledger.jsonl")
            ledger.record(original, status="accepted", policy_version="1", reasons=[])

            self.assertFalse(ledger.needs_processing(original, policy_version="1"))
            self.assertTrue(ledger.needs_processing(changed, policy_version="1"))

    def test_security_fetcher_uses_one_public_updated_window_request(self) -> None:
        class FakeHttp:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def fetch_url(self, url: str) -> bytes:
                self.urls.append(url)
                return b"[]"

        http = FakeHttp()
        payload = GitHubAdvisoryFetcher(http=http).fetch_security("2026-08-31T00:00:00Z")

        self.assertEqual(b"[]", payload)
        self.assertEqual(1, len(http.urls))
        self.assertIn("type=reviewed", http.urls[0])
        self.assertIn("updated=%3E%3D2026-08-31T00%3A00%3A00Z", http.urls[0])
        self.assertIn("per_page=100", http.urls[0])

    def test_known_advisory_is_emitted_when_severity_drops_and_mapping_changes(self) -> None:
        changed = advisory(
            "GHSA-aaaa-bbbb-cccc",
            severity="medium",
            location="https://github.com/other/project",
        )
        records = parse_security_advisories(
            [changed],
            sources=[source()],
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/raw/2026-09-01/github_advisories.json",
            known_entries={
                "GHSA-aaaa-bbbb-cccc": {
                    "repository": "openai/codex",
                    "status": "accepted",
                }
            },
        )

        self.assertEqual(1, len(records))
        self.assertFalse(records[0].metadata["qualifies"])
        self.assertEqual("openai/codex", records[0].repository)


if __name__ == "__main__":
    unittest.main()
