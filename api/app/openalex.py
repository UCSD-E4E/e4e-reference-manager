"""OpenAlex (api.openalex.org): the open scholarly graph. Used for papers' reference
lists, looked up by DOI 50 at a time.

Works are identified by short OpenAlex ids ("W2741809807"); a work's
`referenced_works` lists the ids of the works it cites.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from .ingest import normalize_doi
from .oa_pdf import CONTACT_EMAIL  # OpenAlex's "polite pool" wants a contact address
from .validation import _UA

logger = logging.getLogger("refman")

WORKS_URL = "https://api.openalex.org/works"
BATCH = 50  # DOIs per request (OpenAlex allows up to 50 OR'd values in a filter)


class OpenAlexUnavailable(Exception):
    """OpenAlex couldn't answer (network error, 5xx, bad JSON)."""


def _transport() -> httpx.AsyncBaseTransport | None:
    """Tests swap in an httpx.MockTransport; None = the real network."""
    return None


def _short(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


async def works_by_doi(dois: Iterable[str]) -> dict[str, tuple[str, list[str]]]:
    """{normalized DOI: (OpenAlex id, [ids of the works it cites])} for the DOIs OpenAlex
    knows. Raises OpenAlexUnavailable if any request fails."""
    # `|` separates OR'd filter values and `,` separates filters: such DOIs can't be
    # expressed in a filter (vanishingly rare), so they're skipped.
    wanted = list(
        dict.fromkeys(d for d in map(normalize_doi, dois) if d and not set(d) & {"|", ","})
    )
    out: dict[str, tuple[str, list[str]]] = {}
    try:
        async with httpx.AsyncClient(
            transport=_transport(), timeout=30.0, headers={"User-Agent": _UA}
        ) as client:
            for i in range(0, len(wanted), BATCH):
                r = await client.get(
                    WORKS_URL,
                    params={
                        "filter": "doi:" + "|".join(wanted[i : i + BATCH]),
                        "per-page": BATCH,
                        "select": "id,doi,referenced_works",
                        "mailto": CONTACT_EMAIL,
                    },
                )
                r.raise_for_status()
                for work in r.json().get("results") or []:
                    if (doi := normalize_doi(work.get("doi"))) and work.get("id"):
                        refs = [_short(w) for w in work.get("referenced_works") or []]
                        out[doi] = (_short(work["id"]), refs)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenAlex lookup failed: %s", exc)
        raise OpenAlexUnavailable(str(exc)) from exc
    return out
