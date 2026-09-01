from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {"gclid", "dclid", "fbclid", "igshid", "mc_cid", "mc_eid", "yclid"}
_SPACE = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """Return a deterministic comparison URL without fragments or tracking data."""
    parts = urlsplit(url)
    path = parts.path
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
            ),
            key=lambda pair: (pair[0], pair[1]),
        ),
        doseq=True,
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalize_title(title: str) -> str:
    """Normalize mixed Chinese and ASCII titles without using a semantic model."""
    text = unicodedata.normalize("NFKC", title).lower()
    retained = "".join(character if (character.isalnum() or character.isspace()) else " " for character in text)
    return _SPACE.sub(" ", retained).strip()
