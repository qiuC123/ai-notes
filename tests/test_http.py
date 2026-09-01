from __future__ import annotations

import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.http import PublicHttpFetcher, build_verified_ssl_context
from aihot.pipeline import SourceSpec


class Response:
    content = b"payload"

    def raise_for_status(self) -> None:
        return None


class Client:
    created_with: dict | None = None

    def __init__(self, **kwargs: object) -> None:
        type(self).created_with = kwargs

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> Response:
        return Response()


class HttpBoundaryTests(unittest.TestCase):
    def test_windows_certificate_store_fallback_keeps_tls_verification_enabled(self) -> None:
        context = MagicMock()
        with (
            patch("aihot.http.ssl.create_default_context", side_effect=PermissionError("blocked bundle")),
            patch("aihot.http.ssl.enum_certificates", return_value=[(b"fixture-der", "x509_asn", None)]),
            patch("aihot.http.ssl.DER_cert_to_PEM_cert", return_value="fixture-pem"),
            patch("aihot.http.ssl.SSLContext", return_value=context),
        ):
            context = build_verified_ssl_context()

        self.assertIsNotNone(context)
        self.assertTrue(context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        context.load_verify_locations.assert_called_once_with(cadata="fixture-pem")

    def test_fetcher_passes_a_verified_context_user_agent_timeout_and_redirect_policy(self) -> None:
        verified_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        source = SourceSpec("fixture", "https://fixture.test/feed", "aihot_selected", "json", "primary_official", "Fixture", True)
        with patch("aihot.http.build_verified_ssl_context", return_value=verified_context), patch("aihot.http.httpx.Client", Client):
            payload = PublicHttpFetcher().fetch(source)

        self.assertEqual(b"payload", payload)
        assert Client.created_with is not None
        self.assertIs(verified_context, Client.created_with["verify"])
        self.assertEqual(30.0, Client.created_with["timeout"])
        self.assertTrue(Client.created_with["follow_redirects"])
        self.assertIn("Ai-Notes", Client.created_with["headers"]["User-Agent"])

    def test_fetch_url_reuses_the_verified_public_http_boundary(self) -> None:
        verified_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch("aihot.http.build_verified_ssl_context", return_value=verified_context), patch("aihot.http.httpx.Client", Client):
            payload = PublicHttpFetcher().fetch_url("https://api.github.com/advisories?per_page=1")

        self.assertEqual(b"payload", payload)
        assert Client.created_with is not None
        self.assertIs(verified_context, Client.created_with["verify"])
