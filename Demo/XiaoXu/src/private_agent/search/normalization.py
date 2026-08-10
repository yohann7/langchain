"""Normalization helpers used by search policy."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRAILING_QUERY_PUNCTUATION = "?？!！.。"
_TRACKING_PARAMETERS = {
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
}


def normalize_search_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query)
    return re.sub(r"\s+", " ", normalized).strip()


def search_query_fingerprint(query: str) -> str:
    return normalize_search_query(query).rstrip(_TRAILING_QUERY_PUNCTUATION).rstrip().casefold()


def canonicalize_web_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.casefold()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError):
        return None

    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_PARAMETERS
    ]
    query_items.sort(key=lambda item: (item[0], item[1]))
    return urlunsplit(
        (scheme, netloc, parsed.path or "/", urlencode(query_items, doseq=True), "")
    )
