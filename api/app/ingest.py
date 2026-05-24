"""Metadata ingestion via the Zotero translation-server.

Flow: a DOI/arXiv/PMID/ISBN identifier (or a URL) -> translation-server -> Zotero items
-> exported to CSL-JSON (the translation-server does the CSL conversion for us).
"""
from __future__ import annotations

import re

import httpx

from .config import get_settings


class IngestError(Exception):
    """Raised when metadata lookup/translation fails."""


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d or None


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


async def fetch_csl(query: str) -> list[dict]:
    """Resolve an identifier or URL to a list of CSL-JSON references."""
    s = get_settings()
    base = s.translation_server_url.rstrip("/")
    q = query.strip()
    endpoint = "/web" if q.lower().startswith(("http://", "https://")) else "/search"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                f"{base}{endpoint}", content=q, headers={"Content-Type": "text/plain"}
            )
        except httpx.HTTPError as exc:
            raise IngestError(f"Could not reach translation-server: {exc}") from exc
        if r.status_code == 300:
            raise IngestError("Multiple results — provide a more specific identifier or URL")
        if r.status_code != 200:
            raise IngestError(f"Lookup failed ({r.status_code}): {r.text[:200]}")
        items = r.json()
        if not items:
            raise IngestError("No metadata found for that identifier/URL")

        exp = await client.post(
            f"{base}/export",
            params={"format": "csljson"},
            json=items,
            headers={"Content-Type": "application/json"},
        )
        if exp.status_code != 200:
            raise IngestError(f"CSL export failed ({exp.status_code})")
        csl = exp.json()
    return csl if isinstance(csl, list) else [csl]
