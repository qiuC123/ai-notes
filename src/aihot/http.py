from __future__ import annotations

import time
import ssl
import sys
from dataclasses import dataclass, field
from typing import Protocol

import httpx


PUBLIC_USER_AGENT = "Ai-Notes/0.2 (+https://github.com/openai/codex)"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3


class SourceLike(Protocol):
    source_id: str
    url: str


class FetchError(RuntimeError):
    pass


def build_verified_ssl_context() -> ssl.SSLContext:
    """Use normal verified TLS, with a Windows-store fallback for unreadable bundled CAs."""
    try:
        return ssl.create_default_context()
    except PermissionError:
        if sys.platform != "win32":
            raise
        certificates = ssl.enum_certificates("ROOT")
        ca_data = "".join(
            ssl.DER_cert_to_PEM_cert(certificate)
            for certificate, encoding, _trust in certificates
            if encoding == "x509_asn"
        )
        if not ca_data:
            raise RuntimeError("Windows ROOT certificate store did not provide usable CA certificates")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=ca_data)
        return context


@dataclass(slots=True)
class PublicHttpFetcher:
    """Small bounded-retry client for public, unauthenticated source feeds."""

    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    user_agent: str = PUBLIC_USER_AGENT
    _tls_context: ssl.SSLContext | None = field(default=None, init=False, repr=False)

    def _verified_context(self) -> ssl.SSLContext:
        if self._tls_context is None:
            self._tls_context = build_verified_ssl_context()
        return self._tls_context

    def fetch(self, source: SourceLike) -> bytes:
        return self._fetch_url(source.url, source.source_id)

    def fetch_url(self, url: str) -> bytes:
        return self._fetch_url(url, url)

    def _fetch_url(self, url: str, label: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                    verify=self._verified_context(),
                    headers={"User-Agent": self.user_agent, "Accept": "application/json, application/xml, text/xml, application/atom+xml, */*"},
                ) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    return response.content
            except (httpx.HTTPError, OSError) as error:
                last_error = error
                if attempt < self.max_attempts:
                    time.sleep(2 ** (attempt - 1))
        raise FetchError(f"{label} failed after {self.max_attempts} attempts: {last_error}")
