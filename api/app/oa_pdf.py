"""Find and download a legal open-access copy of a paper's PDF.

Sources, best first: Unpaywall (DOI -> open-access locations: publisher OA, PubMed
Central, institutional repositories), then arXiv, then the reference's own URL (when it
points straight at a public PDF). Paywalled papers with no OA copy can't be fetched.

URLs come from third parties (and from the reference itself, i.e. any editor) and
redirect freely, so every hop is checked: http(s) only, and the host must resolve to
public addresses only (not the slot's own network, e.g. postgres or ollama). The request
then goes to the exact address that was checked — no second DNS lookup that a rebinding
attacker could answer differently — with the original name in the Host header and for
TLS (SNI + certificate verification). Downloads are size- and time-capped and must
actually be a PDF — many "OA" links are HTML landing pages, which are skipped (after
their first bytes) in favour of the next candidate.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import SplitResult, quote, unquote, urljoin, urlsplit

import httpx

from .ingest import normalize_doi
from .netguard import public_addresses
from .validation import _UA, extract_arxiv_id

logger = logging.getLogger("refman")

UNPAYWALL_URL = "https://api.unpaywall.org/v2/"
CONTACT_EMAIL = "ccrutchf@ucsd.edu"  # Unpaywall requires one (same as the Crossref UA)
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)  # per connect/read, not per download
DOWNLOAD_DEADLINE = 120.0  # seconds for one candidate, redirects included


def _transport() -> httpx.AsyncBaseTransport | None:
    """Tests swap in an httpx.MockTransport; None = the real network."""
    return None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_transport(), timeout=_TIMEOUT, headers={"User-Agent": _UA}
    )


def _public_address(host: str) -> str | None:
    """An address to connect to for `host`, or None unless every address it resolves
    to is globally routable. Blocking: call it in a thread."""
    addrs = public_addresses(host)
    return addrs[0] if addrs else None


def _http_url(value: object) -> SplitResult | None:
    """`value` split as an http(s) URL with a host, or None (not a string, malformed,
    another scheme)."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return parts


def _url_key(url: str) -> str:
    """A normalized form for spotting the same URL written differently (case of the
    host, default port, fragment, http vs https)."""
    parts = _http_url(url)
    if parts is None:
        return url
    port = parts.port
    netloc = parts.hostname + (f":{port}" if port not in (None, 80, 443) else "")
    return f"{netloc}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")


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
    if not isinstance(data, dict):
        return []
    locations = [data.get("best_oa_location")] + list(data.get("oa_locations") or [])
    urls: list[str] = []
    for loc in locations:
        url = loc.get("url_for_pdf") if isinstance(loc, dict) else None
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return urls


_DOI_HOSTS = {"doi.org", "dx.doi.org"}
_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}


def _doi(csl: dict) -> str | None:
    """The DOI to look up: the DOI field, else one in a doi.org URL."""
    field = csl.get("DOI")
    if doi := normalize_doi(field if isinstance(field, str) else None):
        return doi
    parts = _http_url(csl.get("URL"))
    if parts and parts.hostname.lower() in _DOI_HOSTS:
        return normalize_doi(unquote(parts.path).lstrip("/"))
    return None


def _reference_url(csl: dict) -> str | None:
    """The reference's own URL, if it's an http(s) link worth trying: not one the DOI
    (Unpaywall) or arXiv candidate already covers."""
    parts = _http_url(csl.get("URL"))
    if parts is None:
        return None
    url = csl["URL"].strip()
    host = parts.hostname.lower()
    if host in _DOI_HOSTS and (own := normalize_doi(unquote(parts.path).lstrip("/"))):
        if own == _doi(csl):
            return None
    if host in _ARXIV_HOSTS and (own := extract_arxiv_id({"URL": url})):
        if own == extract_arxiv_id(csl):
            return None
    return url


def has_identifier(csl: dict) -> bool:
    """Whether there's anything to look up (a DOI, an arXiv id, or a usable URL)."""
    return bool(_doi(csl) or extract_arxiv_id(csl) or _reference_url(csl))


async def find_candidates(csl: dict) -> list[tuple[str, str]]:
    """(source, pdf_url) pairs to try, best first, without repeats."""
    out: list[tuple[str, str]] = []
    if doi := _doi(csl):
        out += [("unpaywall", u) for u in await _unpaywall_pdf_urls(doi)]
    if arxiv_id := extract_arxiv_id(csl):
        out.append(("arxiv", f"https://arxiv.org/pdf/{arxiv_id}"))
    # Last resort: the reference may link straight to a PDF (a lab page, a repository).
    # download_pdf checks it like any other: public host, size cap, really a PDF.
    if url := _reference_url(csl):
        out.append(("url", url))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for source, url in out:
        if (key := _url_key(url)) not in seen:
            seen.add(key)
            unique.append((source, url))
    return unique


async def _read_pdf(r: httpx.Response) -> bytes | None:
    """The body if it's a PDF within the size cap; gives up at the first sign it isn't."""
    try:
        if int(r.headers.get("content-length", 0)) > MAX_PDF_BYTES:
            return None
    except ValueError:
        pass
    data = bytearray()
    async for chunk in r.aiter_bytes():
        data += chunk
        if len(data) >= 4 and not data.startswith(b"%PDF"):
            return None  # an HTML landing page, most likely: don't read the rest
        if len(data) > MAX_PDF_BYTES:
            return None
    return bytes(data) if data.startswith(b"%PDF") else None


async def _download(url: str) -> bytes | None:
    async with _client() as client:
        for _ in range(MAX_REDIRECTS + 1):
            if _http_url(url) is None:
                return None
            target = httpx.URL(url)
            host = target.raw_host.decode("ascii")
            ip = await asyncio.to_thread(_public_address, host)
            if ip is None:
                logger.warning("Refusing non-public PDF host %s", host)
                return None
            async with client.stream(
                "GET",
                target.copy_with(host=ip),  # connect to the address just checked
                headers={"Host": target.netloc.decode("ascii"), "Accept": "application/pdf"},
                extensions={"sni_hostname": host},  # TLS: SNI + cert check by name
            ) as r:
                if r.is_redirect and "location" in r.headers:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    return None
                return await _read_pdf(r)
    return None


async def download_pdf(url: str) -> bytes | None:
    """Fetch `url` (following safe redirects); the bytes if it's a PDF, else None."""
    try:
        async with asyncio.timeout(DOWNLOAD_DEADLINE):
            return await _download(url)
    except TimeoutError:
        logger.warning("PDF download from %s took longer than %ss", url, DOWNLOAD_DEADLINE)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, UnicodeError) as exc:
        logger.warning("PDF download failed for %s: %s", url, exc)
    return None


async def fetch_open_access_pdf(csl: dict) -> tuple[bytes, str, str] | None:
    """(pdf bytes, source, url) for the first candidate that is really a PDF."""
    for source, url in await find_candidates(csl):
        if data := await download_pdf(url):
            return data, source, url
    return None
