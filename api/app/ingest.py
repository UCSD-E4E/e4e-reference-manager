"""Metadata ingestion via the Zotero translation-server.

Flow: a DOI/arXiv/PMID/ISBN identifier (or a URL) -> translation-server -> Zotero items
-> exported to CSL-JSON (the translation-server does the CSL conversion for us).
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

import httpx

from .config import get_settings
from .netguard import public_addresses


class IngestError(Exception):
    """Raised when metadata lookup/translation fails."""


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", d)
    return d or None


def normalize_title(title: str | None) -> str | None:
    """Case/punctuation/BibTeX-brace-insensitive title, for duplicate detection."""
    t = re.sub(r"[{}]", "", (title or "").lower())  # BibTeX case-protection braces
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return t or None


def gen_citation_key(csl: dict) -> str:
    authors = csl.get("author") or []
    family = (authors[0].get("family") if authors else "") or "ref"
    parts = csl.get("issued", {}).get("date-parts", [[None]])
    year = str(parts[0][0]) if parts and parts[0] and parts[0][0] else ""
    title_word = ""
    for w in (csl.get("title") or "").split():
        cleaned = re.sub(r"[^a-z0-9]", "", w.lower())
        if len(cleaned) > 3:
            title_word = cleaned
            break
    return f"{re.sub(r'[^A-Za-z0-9]', '', family).lower()}{year}{title_word}" or "ref"


def _transport() -> httpx.AsyncBaseTransport | None:
    """Tests swap in an httpx.MockTransport; None = the real network."""
    return None


async def _check_public_url(url: str) -> None:
    """translation-server fetches /web URLs from inside our private network, so only
    send it URLs whose host resolves to public addresses (not postgres, ollama, ...)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    if not host:
        raise IngestError("That URL is not valid")
    if not await asyncio.to_thread(public_addresses, host):
        raise IngestError(f"That URL's host ({host}) is not a public internet address")


def _unreachable(exc: httpx.HTTPError) -> IngestError:
    # Some httpx errors (timeouts) have an empty message: name the error type too.
    return IngestError(
        f"Could not reach translation-server: {type(exc).__name__} {exc}".strip()
    )


async def fetch_csl(query: str) -> list[dict]:
    """Resolve an identifier or URL to a list of CSL-JSON references."""
    s = get_settings()
    base = s.translation_server_url.rstrip("/")
    q = query.strip()
    is_url = q.lower().startswith(("http://", "https://"))
    if is_url:
        await _check_public_url(q)
    endpoint = "/web" if is_url else "/search"
    async with httpx.AsyncClient(timeout=30.0, transport=_transport()) as client:
        try:
            r = await client.post(
                f"{base}{endpoint}", content=q, headers={"Content-Type": "text/plain"}
            )
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc
        if r.status_code == 300:
            raise IngestError("Multiple results — provide a more specific identifier or URL")
        if r.status_code != 200:
            raise IngestError(f"Lookup failed ({r.status_code}): {r.text[:200]}")
        try:
            items = r.json()
        except ValueError as exc:
            raise IngestError("translation-server returned an unreadable response") from exc
        if not items:
            raise IngestError("No metadata found for that identifier/URL")

        try:
            exp = await client.post(
                f"{base}/export",
                params={"format": "csljson"},
                json=items,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc
        if exp.status_code != 200:
            raise IngestError(f"CSL export failed ({exp.status_code})")
        try:
            csl = exp.json()
        except ValueError as exc:
            raise IngestError("CSL export returned an unreadable response") from exc
    return csl if isinstance(csl, list) else [csl]
