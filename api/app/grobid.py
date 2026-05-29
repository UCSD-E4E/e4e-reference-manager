"""GROBID client: PDF -> header metadata, parsed into a CSL-JSON dict."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from .config import get_settings
from .ingest import IngestError

TEI = "{http://www.tei-c.org/ns/1.0}"


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def parse_tei_header(xml_text: str) -> dict:
    """Extract title/authors/DOI/abstract/year from GROBID TEI into a CSL-JSON dict."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise IngestError(f"Could not parse GROBID output: {exc}") from exc

    csl: dict = {"type": "article-journal"}

    title = root.find(f".//{TEI}fileDesc/{TEI}titleStmt/{TEI}title")
    if _text(title):
        csl["title"] = _text(title)

    authors = []
    for pers in root.findall(
        f".//{TEI}sourceDesc/{TEI}biblStruct/{TEI}analytic/{TEI}author/{TEI}persName"
    ):
        given = " ".join(_text(f) for f in pers.findall(f"{TEI}forename")).strip()
        family = _text(pers.find(f"{TEI}surname"))
        if family or given:
            authors.append({"family": family, "given": given} if given else {"family": family})
    if authors:
        csl["author"] = authors

    doi = root.find(f".//{TEI}idno[@type='DOI']")
    if _text(doi):
        csl["DOI"] = _text(doi).lower()

    abstract = root.find(f".//{TEI}profileDesc/{TEI}abstract")
    if _text(abstract):
        csl["abstract"] = _text(abstract)

    date = root.find(f".//{TEI}publicationStmt/{TEI}date[@when]")
    when = date.get("when") if date is not None else None
    if when and when[:4].isdigit():
        csl["issued"] = {"date-parts": [[int(when[:4])]]}

    return csl


async def extract_header_csl(pdf: bytes) -> dict:
    s = get_settings()
    base = s.grobid_url.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(
                f"{base}/api/processHeaderDocument",
                headers={"Accept": "application/xml"},  # else GROBID returns BibTeX
                files={"input": ("document.pdf", pdf, "application/pdf")},
                data={"consolidateHeader": "0"},
            )
        except httpx.HTTPError as exc:
            raise IngestError(f"Could not reach GROBID: {exc}") from exc
    if r.status_code != 200:
        raise IngestError(f"GROBID failed ({r.status_code})")
    return parse_tei_header(r.text)


def _authors_from(parent: ET.Element) -> list[dict]:
    out: list[dict] = []
    for pers in parent.findall(f".//{TEI}author/{TEI}persName"):
        given = " ".join(_text(f) for f in pers.findall(f"{TEI}forename")).strip()
        family = _text(pers.find(f"{TEI}surname"))
        if family or given:
            out.append({"family": family, "given": given} if given else {"family": family})
    return out


def parse_tei_references(xml_text: str) -> list[dict]:
    """Parse GROBID processReferences output into CSL-like dicts (one per cited work).

    Each dict carries `title`, optional `DOI`, optional `author`, optional
    `issued.date-parts`, and `_raw` (the original cited text, when GROBID provides it via
    <note type="raw_reference">). Suitable for feeding straight into
    `validation.validate_item_csl`.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    refs: list[dict] = []
    for bibl in root.findall(f".//{TEI}listBibl/{TEI}biblStruct"):
        analytic = bibl.find(f"{TEI}analytic")
        monogr = bibl.find(f"{TEI}monogr")

        title = ""
        if analytic is not None:
            title = _text(analytic.find(f"{TEI}title"))
        if not title and monogr is not None:
            title = _text(monogr.find(f"{TEI}title"))

        authors = _authors_from(analytic) if analytic is not None else []
        if not authors and monogr is not None:
            authors = _authors_from(monogr)

        doi_el = bibl.find(f".//{TEI}idno[@type='DOI']")
        doi = _text(doi_el).lower() or None

        year = None
        date_el = bibl.find(f".//{TEI}date[@when]")
        when = date_el.get("when") if date_el is not None else None
        if when and when[:4].isdigit():
            year = int(when[:4])

        raw_el = bibl.find(f"{TEI}note[@type='raw_reference']")
        raw = _text(raw_el)

        ref: dict = {"title": title}
        if authors:
            ref["author"] = authors
        if doi:
            ref["DOI"] = doi
        if year is not None:
            ref["issued"] = {"date-parts": [[year]]}
        if raw:
            ref["_raw"] = raw
        refs.append(ref)
    return refs


async def extract_references(pdf: bytes) -> list[dict]:
    """GROBID processReferences -> list of CSL-like dicts. Returns [] on any failure
    (callers treat that as "couldn't extract any citations")."""
    s = get_settings()
    base = s.grobid_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{base}/api/processReferences",
                headers={"Accept": "application/xml"},
                files={"input": ("document.pdf", pdf, "application/pdf")},
                data={"consolidateCitations": "0"},
            )
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    return parse_tei_references(r.text)
