"""BibTeX <-> CSL-JSON conversion.

We keep the raw BibTeX entry for lossless export (preserving citation keys exactly) and
also parse a best-effort CSL-JSON representation for display, search, and editing.
"""
from __future__ import annotations

import re
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


def _split_on_and(value: str) -> list[str]:
    """Split on ' and ' outside braces (`{Barnes and Noble}` is one name)."""
    names, depth, start = [], 0, 0
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
        elif depth == 0 and value.startswith(" and ", i):
            names.append(value[start:i])
            start = i = i + len(" and ")
            continue
        i += 1
    names.append(value[start:])
    return names


def _split_authors(value: str) -> list[dict]:
    """Convert a BibTeX 'and'-separated author string to CSL name objects."""
    people = []
    for raw in _split_on_and(re.sub(r"\s+", " ", value)):
        name = raw.strip()
        if not name:
            continue
        if re.fullmatch(r"\{[^{}]*\}", name):  # "{World Health Organization}"
            people.append({"literal": name[1:-1].strip()})
        elif "," in name:  # "Family, Given"
            family, _, given = name.partition(",")
            people.append({"family": family.strip(), "given": given.strip()})
        else:  # "Given ... Family"
            parts = name.split()
            if len(parts) == 1:
                people.append({"family": parts[0]})
            else:
                people.append({"family": parts[-1], "given": " ".join(parts[:-1])})
    return people


_UNESCAPED_BRACE = re.compile(r"(?<!\\)[{}]")


def _strip_braces(value: str) -> str:
    """Drop BibTeX's protective braces (`{DSL} Modems`, `{{Whole Title}}`) but keep
    escaped ones (`\\{`)."""
    return _UNESCAPED_BRACE.sub("", value).strip()


def _entry_to_csl(entry: dict) -> tuple[str, dict]:
    bib_type = entry.get("ENTRYTYPE", "misc").lower()
    csl_type = _BIBTEX_TO_CSL_TYPE.get(bib_type, "document")
    csl: dict = {"type": csl_type, "id": entry.get("ID", "")}

    if title := entry.get("title"):
        csl["title"] = _strip_braces(title)
    if author := entry.get("author"):
        csl["author"] = _split_authors(author)
    if editor := entry.get("editor"):
        csl["editor"] = _split_authors(editor)
    if journal := (entry.get("journal") or entry.get("journaltitle")):
        csl["container-title"] = _strip_braces(journal)
    elif booktitle := entry.get("booktitle"):
        csl["container-title"] = _strip_braces(booktitle)
    if publisher := entry.get("publisher"):
        csl["publisher"] = _strip_braces(publisher)
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
    if (year := _valid_year(entry.get("year"))) is not None:
        csl["issued"] = {"date-parts": [[year]]}
    return csl_type, csl


def _valid_year(value: object) -> int | None:
    """An int year in 1..9999, else None (a typo like 99999999999 would overflow the
    year column and fail the whole import)."""
    if isinstance(value, bool):
        return None
    try:
        year = int(value) if isinstance(value, (int, float)) else int(str(value).strip())
    except (ValueError, TypeError, OverflowError):
        return None
    return year if 1 <= year <= 9999 else None


def _single_entry_bibtex(entry: dict) -> str:
    db = BibDatabase()
    db.entries = [entry]
    writer = BibTexWriter()
    writer.indent = "  "
    return bibtexparser.dumps(db, writer).strip()


def parse_bibtex(text: str) -> list[ParsedEntry]:
    text = text.replace("\x00", "")  # Postgres text can't hold NUL
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


def _balance_braces(value: str) -> str:
    """Drop unmatched braces: an unbalanced value would swallow the entries after it."""
    out: list[str] = []
    opened: list[int] = []  # positions in `out` of still-open braces
    for ch in value:
        if ch == "{":
            opened.append(len(out))
        elif ch == "}":
            if not opened:
                continue
            opened.pop()
        out.append(ch)
    for pos in reversed(opened):
        del out[pos]
    return "".join(out)


_LATEX_SPECIAL = re.compile(r"(?<!\\)([&%$#_])")


def _bib_text(value: object) -> str:
    """A generated field value: LaTeX specials escaped, braces balanced."""
    return _balance_braces(_LATEX_SPECIAL.sub(r"\\\1", str(value)))


def _bib_verbatim(value: object) -> str:
    """url/doi are read verbatim by biblatex: no escaping, but braces still balanced."""
    return _balance_braces(str(value))


def _bib_key(key: str) -> str:
    """A citation key BibTeX can parse (no spaces, braces, commas, quotes...)."""
    return re.sub(r"[\s{}(),\"#%'=~\\]", "", key)


def _csl_names_to_bibtex(names: list) -> str:
    parts = []
    for n in names:
        if not isinstance(n, dict):
            continue
        if literal := n.get("literal"):  # an organisation: braced so it isn't split
            parts.append("{" + _bib_text(literal) + "}")
            continue
        family = _bib_text(n.get("family") or "")
        given = _bib_text(n.get("given") or "")
        parts.append(f"{family}, {given}".strip(", ") if given else family)
    return " and ".join(p for p in parts if p)


def csl_to_bibtex_entry(citation_key: str, csl: dict) -> dict:
    bib_type = _CSL_TO_BIBTEX_TYPE.get(csl.get("type", "document"), "misc")
    key = _bib_key(str(citation_key or csl.get("id") or "")) or "ref"
    entry: dict = {"ENTRYTYPE": bib_type, "ID": key}
    if csl.get("title"):
        entry["title"] = _bib_text(csl["title"])
    for role in ("author", "editor"):
        if isinstance(csl.get(role), list) and (names := _csl_names_to_bibtex(csl[role])):
            entry[role] = names
    if csl.get("container-title"):
        field = "journal" if bib_type == "article" else "booktitle"
        entry[field] = _bib_text(csl["container-title"])
    for csl_key, bib_key in (
        ("publisher", "publisher"),
        ("volume", "volume"),
        ("issue", "number"),
        ("page", "pages"),
        ("abstract", "abstract"),
    ):
        if csl.get(csl_key):
            entry[bib_key] = _bib_text(csl[csl_key])
    for csl_key, bib_key in (("DOI", "doi"), ("URL", "url")):
        if csl.get(csl_key):
            entry[bib_key] = _bib_verbatim(csl[csl_key])
    if (year := year_from_csl(csl)) is not None:
        entry["year"] = str(year)
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
    """The issued year, if it's a plausible one (1..9999; fits the year column)."""
    issued = csl.get("issued")
    parts = issued.get("date-parts") if isinstance(issued, dict) else None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        return _valid_year(parts[0][0])
    return None
