"""Find and download a legal open-access copy of a paper's PDF.

Sources, best first: Unpaywall (DOI -> open-access locations: publisher OA, PubMed
Central, institutional repositories), then arXiv. Paywalled papers with no OA copy
can't be fetched.

URLs come from third parties and redirect freely, so every hop is checked: http(s)
only, and the host must resolve to public addresses (not the slot's own network, e.g.
postgres or ollama). Downloads are size-capped and must actually be a PDF — many "OA"
links are HTML landing pages, which are skipped in favour of the next candidate.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .ingest import normalize_doi
from .validation import _UA, extract_arxiv_id

logger = logging.getLogger("refman")

UNPAYWALL_URL = "https://api.unpaywall.org/v2/"
CONTACT_EMAIL = "ccrutchf@ucsd.edu"  # Unpaywall requires one (same as the Crossref UA)
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _transport() -> httpx.AsyncBaseTransport | None:
    """Tests swap in an httpx.MockTransport; None = the real network."""
    return None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_transport(), timeout=_TIMEOUT, headers={"User-Agent": _UA}
    )


def _resolves_to_public(host: str) -> bool:
    """True only if every address `host` resolves to is globally routable. (A DNS answer
    could still change before httpx connects; this blocks the realistic cases.)"""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def unpaywall_url(doi: str) -> str:
    return str(httpx.URL(UNPAYWALL_URL + quote(doi, safe="/"), params={"email": CONTACT_EMAIL}))


async def _unpaywall_pdf_urls(doi: str) -> list[str]:
    try:
        async with _client() as client:
            r = await client.get(unpaywall_url(doi))
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Unpaywall lookup failed for %s: %s", doi, exc)
        return []
    locations = [data.get("best_oa_location")] + list(data.get("oa_locations") or [])
    urls: list[str] = []
    for loc in locations:
        url = (loc or {}).get("url_for_pdf")
        if url and url not in urls:
            urls.append(url)
    return urls


async def find_candidates(csl: dict) -> list[tuple[str, str]]:
    """(source, pdf_url) pairs to try, best first."""
    out: list[tuple[str, str]] = []
    if doi := normalize_doi(csl.get("DOI")):
        out += [("unpaywall", u) for u in await _unpaywall_pdf_urls(doi)]
    if arxiv_id := extract_arxiv_id(csl):
        out.append(("arxiv", f"https://arxiv.org/pdf/{arxiv_id}"))
    return out


async def download_pdf(url: str) -> bytes | None:
    """Fetch `url` (following safe redirects); the bytes if it's a PDF, else None."""
    try:
        async with _client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    return None
                if not await asyncio.to_thread(_resolves_to_public, parts.hostname):
                    logger.warning("Refusing non-public PDF host %s", parts.hostname)
                    return None
                async with client.stream(
                    "GET", url, headers={"Accept": "application/pdf"}
                ) as r:
                    if r.is_redirect and "location" in r.headers:
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        return None
                    data = bytearray()
                    async for chunk in r.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_PDF_BYTES:
                            return None
                    return bytes(data) if data.startswith(b"%PDF") else None
    except httpx.HTTPError as exc:
        logger.warning("PDF download failed for %s: %s", url, exc)
    return None


async def fetch_open_access_pdf(csl: dict) -> tuple[bytes, str, str] | None:
    """(pdf bytes, source, url) for the first candidate that is really a PDF."""
    for source, url in await find_candidates(csl):
        if data := await download_pdf(url):
            return data, source, url
    return None
