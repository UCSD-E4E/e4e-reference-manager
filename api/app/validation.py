"""Anti-hallucination validation: look up references at canonical registrars and
compare titles to catch fabricated DOIs / made-up papers.

Strategy:
- DOI present  -> Crossref `/works/{doi}`, then DataCite (arXiv's 10.48550 DOIs, Zenodo,
                  datasets) if Crossref doesn't hold it. Unknown to both = not_found; a
                  title mismatch = the DOI resolves to a *different* real paper, which is
                  the classic AI-fabrication signature.
- arXiv id     -> arXiv API; same comparison.
- Otherwise    -> Crossref title search; verified only if the top hit's title matches.

All HTTP is monkeypatched in tests. A registry that can't be reached gives
`unverifiable` (an outage says nothing about whether a paper exists), never a 500 and
never `not_found`."""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .ingest import normalize_doi

logger = logging.getLogger("refman")

CROSSREF_URL = "https://api.crossref.org/works"
# DOIs Crossref doesn't hold (arXiv's 10.48550/arXiv.*, Zenodo, figshare, datasets...) are
# mostly registered with DataCite.
DATACITE_URL = "https://api.datacite.org/dois"
ARXIV_URL = "http://export.arxiv.org/api/query"
# Polite-pool UA per Crossref guidance (https://api.crossref.org swagger).
_UA = "e4e-reference-manager (mailto:ccrutchf@ucsd.edu)"
_HEADERS = {"User-Agent": _UA, "Accept": "application/json"}

# How close the registrar's title must be to the cited title to count as "verified".
TITLE_MATCH_THRESHOLD = 0.6

_ATOM = "{http://www.w3.org/2005/Atom}"
# arXiv:2101.00001, arxiv/2101.00001, arxiv.org/abs|pdf/2101.00001, 10.48550/arXiv.2101.00001
_ARXIV_ID_RE = re.compile(
    r"arxiv(?:\.org/(?:abs|pdf)/|[:/.]\s*)([0-9]{4}\.[0-9]{4,5})", re.IGNORECASE
)


# ---------- pure helpers ----------

def normalize_title(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(a: str | None, b: str | None) -> float:
    """Token Jaccard over normalized titles. Robust to punctuation/case/word-order."""
    aw = set(normalize_title(a).split())
    bw = set(normalize_title(b).split())
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


def parse_crossref_work(js: dict) -> dict:
    """Flatten a Crossref response (single or first hit) into {title, DOI, year}."""
    msg = js.get("message") if isinstance(js.get("message"), dict) else js
    titles = msg.get("title") or []
    title = titles[0] if titles else ""
    doi = (msg.get("DOI") or "").lower() or None
    issued = msg.get("issued") or {}
    parts = issued.get("date-parts") or []
    year = parts[0][0] if parts and parts[0] else None
    return {"title": title, "DOI": doi, "year": year}


def parse_arxiv_atom(xml_text: str) -> dict | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return None
    title_el = entry.find(f"{_ATOM}title")
    pub_el = entry.find(f"{_ATOM}published")
    title = (title_el.text or "").strip() if title_el is not None else ""
    year = None
    if pub_el is not None and pub_el.text:
        head = pub_el.text[:4]
        if head.isdigit():
            year = int(head)
    return {"title": title, "year": year}


def extract_arxiv_id(csl: dict) -> str | None:
    """Pull an arXiv id out of common CSL fields (note/URL/DOI)."""
    for key in ("note", "URL", "DOI"):
        val = csl.get(key)
        if isinstance(val, str):
            m = _ARXIV_ID_RE.search(val)
            if m:
                return m.group(1)
    return None


# ---------- HTTP (best-effort; tests monkeypatch these) ----------


class RegistryUnavailable(Exception):
    """A registry couldn't be asked (network error, 5xx): unlike a 404, this says nothing
    about whether the DOI exists."""


async def _registry_get(registry: str, url: str) -> dict | None:
    """GET a registry's JSON record: None on 404 (no such DOI), RegistryUnavailable if the
    registry couldn't answer."""
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
            r = await client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("%s lookup failed for %s: %s", registry, url, exc)
        raise RegistryUnavailable(f"{registry}: {exc}") from exc


async def crossref_lookup(doi: str) -> dict | None:
    if not doi:
        return None
    # DOIs may contain '#', '?', '%'...: quote them so they stay in the path.
    js = await _registry_get("Crossref", f"{CROSSREF_URL}/{quote(doi, safe='/')}")
    return parse_crossref_work(js) if js is not None else None


async def datacite_lookup(doi: str) -> dict | None:
    if not doi:
        return None
    js = await _registry_get("DataCite", f"{DATACITE_URL}/{quote(doi, safe='/')}")
    if js is None:
        return None
    attrs = (js.get("data") or {}).get("attributes") or {}
    titles = attrs.get("titles") or [{}]
    year = attrs.get("publicationYear")
    return {
        "title": titles[0].get("title", ""),
        "DOI": doi,
        "year": int(year) if str(year or "").isdigit() else None,
    }


async def crossref_search(title: str, authors: list | None = None) -> dict | None:
    if not title or not title.strip():
        return None
    params: dict = {"query.title": title, "rows": 1}
    if authors:
        # Take the first author's family name as the strongest cheap signal.
        first = next((a for a in authors if isinstance(a, dict) and a.get("family")), None)
        if first:
            params["query.author"] = first["family"]
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
            r = await client.get(CROSSREF_URL, params=params)
        r.raise_for_status()
        items = (r.json().get("message") or {}).get("items") or []
        if not items:
            return None
        return parse_crossref_work({"message": items[0]})
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Crossref search failed for %r: %s", title, exc)
        return None


async def arxiv_lookup(arxiv_id: str) -> dict | None:
    if not arxiv_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(ARXIV_URL, params={"id_list": arxiv_id})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("arXiv lookup failed for %s: %s", arxiv_id, exc)
        return None
    return parse_arxiv_atom(r.text)


# ---------- orchestrator ----------

def _verdict(
    status: str,
    source: str,
    *,
    matched_title: str | None = None,
    title_similarity_value: float | None = None,
    notes: str = "",
) -> dict:
    return {
        "status": status,  # verified | metadata_mismatch | not_found | unverifiable
        "source": source,  # crossref | datacite | arxiv | none
        "matched_title": matched_title,
        "title_similarity": title_similarity_value,
        "notes": notes,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def validate_item_csl(csl: dict) -> dict:
    title = csl.get("title") or ""
    raw_doi = csl.get("DOI")
    # "https://doi.org/10.1/x" and "doi:10.1/x" are the same DOI as "10.1/x"
    doi = normalize_doi(raw_doi if isinstance(raw_doi, str) else None) or ""

    if doi:
        # Crossref holds most journal/conference DOIs; DataCite holds arXiv's, Zenodo's and
        # other repositories'. A DOI unknown to both is not_found; a registry that can't
        # be reached proves nothing either way.
        try:
            work, source = await crossref_lookup(doi), "crossref"
            if work is None:
                work, source = await datacite_lookup(doi), "datacite"
        except RegistryUnavailable as exc:
            return _verdict(
                "unverifiable", "none", notes=f"Couldn't reach a DOI registry ({exc})"
            )
        if work is None:
            return _verdict(
                "not_found",
                "crossref",
                notes=f"DOI {doi} is registered with neither Crossref nor DataCite",
            )
        sim = title_similarity(title, work.get("title", ""))
        status = "verified" if sim >= TITLE_MATCH_THRESHOLD else "metadata_mismatch"
        notes = (
            "DOI resolves and titles match"
            if status == "verified"
            else "DOI resolves but points to a different paper than cited"
        )
        return _verdict(
            status,
            source,
            matched_title=work.get("title"),
            title_similarity_value=sim,
            notes=notes,
        )

    arxiv_id = extract_arxiv_id(csl)
    if arxiv_id:
        work = await arxiv_lookup(arxiv_id)
        if work is None:
            return _verdict(
                "not_found", "arxiv", notes=f"arXiv id {arxiv_id} did not resolve"
            )
        sim = title_similarity(title, work.get("title", ""))
        status = "verified" if sim >= TITLE_MATCH_THRESHOLD else "metadata_mismatch"
        return _verdict(
            status,
            "arxiv",
            matched_title=work.get("title"),
            title_similarity_value=sim,
            notes="arXiv id resolves" + ("" if status == "verified" else " but title differs"),
        )

    if title.strip():
        work = await crossref_search(title, csl.get("author"))
        if work is None:
            return _verdict(
                "unverifiable",
                "crossref",
                notes="No DOI/arXiv id and Crossref title search returned no hits",
            )
        sim = title_similarity(title, work.get("title", ""))
        if sim >= TITLE_MATCH_THRESHOLD:
            return _verdict(
                "verified",
                "crossref",
                matched_title=work.get("title"),
                title_similarity_value=sim,
                notes="Matched a Crossref record by title",
            )
        return _verdict(
            "unverifiable",
            "crossref",
            matched_title=work.get("title"),
            title_similarity_value=sim,
            notes="Top Crossref title-search hit did not match closely enough",
        )

    return _verdict("unverifiable", "none", notes="No DOI, arXiv id, or title to check")


# ---------- short-lived verdict cache ----------
# A paste preview validates entries, then "add" stores verdicts for the chosen ones; the
# cache lets the add reuse the preview's lookups instead of hitting Crossref twice. In
# process memory: one API worker, and a miss just means looking it up again.

_CACHE_TTL_S = 3600
_CACHE_MAX = 2000
_cache: dict[str, tuple[float, dict]] = {}


def clear_validation_cache() -> None:
    _cache.clear()


async def cached_validate_csl(csl: dict) -> dict:
    key = json.dumps(csl, sort_keys=True, default=str)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    verdict = await validate_item_csl(csl)
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))  # oldest insertion
    _cache[key] = (time.monotonic(), verdict)
    return verdict
