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
                files={"input": ("document.pdf", pdf, "application/pdf")},
                data={"consolidateHeader": "0"},
            )
        except httpx.HTTPError as exc:
            raise IngestError(f"Could not reach GROBID: {exc}") from exc
    if r.status_code != 200:
        raise IngestError(f"GROBID failed ({r.status_code})")
    return parse_tei_header(r.text)
