"""BibTeX <-> CSL-JSON conversion.

We keep the raw BibTeX entry for lossless export (preserving citation keys exactly) and
also parse a best-effort CSL-JSON representation for display, search, and editing.
"""
from __future__ import annotations

from dataclasses import dataclass

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

# BibTeX entry type -> CSL type (common cases; default "document")
_BIBTEX_TO_CSL_TYPE = {
    "article": "article-journal",
    "book": "book",
    "booklet": "book",
    "inbook": "chapter",
    "incollection": "chapter",
    "inproceedings": "paper-conference",
    "conference": "paper-conference",
    "proceedings": "book",
    "manual": "report",
    "mastersthesis": "thesis",
    "phdthesis": "thesis",
    "techreport": "report",
    "misc": "document",
    "unpublished": "manuscript",
    "online": "webpage",
    "electronic": "webpage",
}
_CSL_TO_BIBTEX_TYPE = {
    "article-journal": "article",
    "article": "article",
    "book": "book",
    "chapter": "incollection",
    "paper-conference": "inproceedings",
    "report": "techreport",
    "thesis": "phdthesis",
    "manuscript": "unpublished",
    "webpage": "online",
    "document": "misc",
}


@dataclass
class ParsedEntry:
    citation_key: str
    csl_type: str
    csl_json: dict
    raw_bibtex: str


def _split_authors(value: str) -> list[dict]:
    """Convert a BibTeX 'and'-separated author string to CSL name objects."""
    people = []
    for raw in value.replace("\n", " ").split(" and "):
        name = raw.strip()
        if not name:
            continue
        if "," in name:  # "Family, Given"
            family, _, given = name.partition(",")
            people.append({"family": family.strip(), "given": given.strip()})
        else:  # "Given ... Family"
            parts = name.split()
            if len(parts) == 1:
                people.append({"family": parts[0]})
            else:
                people.append({"family": parts[-1], "given": " ".join(parts[:-1])})
    return people


def _entry_to_csl(entry: dict) -> tuple[str, dict]:
    bib_type = entry.get("ENTRYTYPE", "misc").lower()
    csl_type = _BIBTEX_TO_CSL_TYPE.get(bib_type, "document")
    csl: dict = {"type": csl_type, "id": entry.get("ID", "")}

    if title := entry.get("title"):
        csl["title"] = title.strip("{}").strip()
    if author := entry.get("author"):
        csl["author"] = _split_authors(author)
    if editor := entry.get("editor"):
        csl["editor"] = _split_authors(editor)
    if journal := (entry.get("journal") or entry.get("journaltitle")):
        csl["container-title"] = journal
    elif booktitle := entry.get("booktitle"):
        csl["container-title"] = booktitle
    if publisher := entry.get("publisher"):
        csl["publisher"] = publisher
    if volume := entry.get("volume"):
        csl["volume"] = volume
    if number := entry.get("number"):
        csl["issue"] = number
    if pages := entry.get("pages"):
        csl["page"] = pages.replace("--", "-")
    if doi := entry.get("doi"):
        csl["DOI"] = doi
    if url := entry.get("url"):
        csl["URL"] = url
    if abstract := entry.get("abstract"):
        csl["abstract"] = abstract
    if year := entry.get("year"):
        try:
            csl["issued"] = {"date-parts": [[int(year)]]}
        except ValueError:
            pass
    return csl_type, csl


def _single_entry_bibtex(entry: dict) -> str:
    db = BibDatabase()
    db.entries = [entry]
    writer = BibTexWriter()
    writer.indent = "  "
    return bibtexparser.dumps(db, writer).strip()


def parse_bibtex(text: str) -> list[ParsedEntry]:
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    db = bibtexparser.loads(text, parser=parser)
    results: list[ParsedEntry] = []
    for entry in db.entries:
        csl_type, csl = _entry_to_csl(entry)
        results.append(
            ParsedEntry(
                citation_key=entry.get("ID", ""),
                csl_type=csl_type,
                csl_json=csl,
                raw_bibtex=_single_entry_bibtex(entry),
            )
        )
    return results


def _csl_names_to_bibtex(names: list[dict]) -> str:
    parts = []
    for n in names:
        family = n.get("family", "")
        given = n.get("given", "")
        parts.append(f"{family}, {given}".strip(", ") if given else family)
    return " and ".join(p for p in parts if p)


def csl_to_bibtex_entry(citation_key: str, csl: dict) -> dict:
    bib_type = _CSL_TO_BIBTEX_TYPE.get(csl.get("type", "document"), "misc")
    entry: dict = {"ENTRYTYPE": bib_type, "ID": citation_key or csl.get("id") or "ref"}
    if "title" in csl:
        entry["title"] = csl["title"]
    if csl.get("author"):
        entry["author"] = _csl_names_to_bibtex(csl["author"])
    if csl.get("editor"):
        entry["editor"] = _csl_names_to_bibtex(csl["editor"])
    if "container-title" in csl:
        key = "journal" if bib_type == "article" else "booktitle"
        entry[key] = csl["container-title"]
    for csl_key, bib_key in (
        ("publisher", "publisher"),
        ("volume", "volume"),
        ("issue", "number"),
        ("page", "pages"),
        ("DOI", "doi"),
        ("URL", "url"),
        ("abstract", "abstract"),
    ):
        if csl.get(csl_key):
            entry[bib_key] = str(csl[csl_key])
    issued = csl.get("issued", {}).get("date-parts", [[None]])
    if issued and issued[0] and issued[0][0]:
        entry["year"] = str(issued[0][0])
    return entry


def build_bibtex(items: list[tuple[str, dict, str | None]]) -> str:
    """Render items to a .bib document.

    Each item is (citation_key, csl_json, raw_bibtex). When raw_bibtex is present we
    re-emit it verbatim for fidelity; otherwise we generate from CSL-JSON.
    """
    raw_chunks: list[str] = []
    generated: list[dict] = []
    for citation_key, csl, raw in items:
        if raw:
            raw_chunks.append(raw.strip())
        else:
            generated.append(csl_to_bibtex_entry(citation_key, csl))
    out = "\n\n".join(raw_chunks)
    if generated:
        db = BibDatabase()
        db.entries = generated
        writer = BibTexWriter()
        writer.indent = "  "
        out = (out + "\n\n" + bibtexparser.dumps(db, writer)).strip()
    return out.strip() + "\n"


def year_from_csl(csl: dict) -> int | None:
    parts = csl.get("issued", {}).get("date-parts", [[None]])
    if parts and parts[0] and parts[0][0]:
        try:
            return int(parts[0][0])
        except (ValueError, TypeError):
            return None
    return None
