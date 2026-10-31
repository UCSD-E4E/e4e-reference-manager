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

from .validation import extract_arxiv_id

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
# CSL has one "thesis" type and says which kind in `genre`; BibTeX has an entry type per
# kind. Each kind's default genre, and how to recognise a master's genre ("MS thesis").
_THESIS_GENRE = {"mastersthesis": "Master's thesis", "phdthesis": "PhD thesis"}
_MASTERS_GENRE = re.compile(r"^\s*(master|m\.?\s?(s|sc|a|eng)\b)", re.IGNORECASE)


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


# Plain-text fields carried both ways (import: BibTeX → CSL; export: CSL → BibTeX).
# `location` is biblatex's name for `address`; `howpublished` is how  says what
# kind of thing it is ("Preprint"), which CSL calls `genre`.
_BIB_TO_CSL_TEXT = (
    ("address", "publisher-place"),
    ("location", "publisher-place"),
    ("isbn", "ISBN"),
    ("issn", "ISSN"),
    ("note", "note"),
    ("edition", "edition"),
    ("howpublished", "genre"),
)


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
    for bib_key, csl_key in _BIB_TO_CSL_TEXT:
        if (value := entry.get(bib_key)) and csl_key not in csl:
            csl[csl_key] = _strip_braces(value)
    # arXiv eprints (`eprint` + `archiveprefix`, or biblatex's `eprinttype`). A @misc
    # one is a preprint: CSL type "article".
    prefix = (entry.get("archiveprefix") or entry.get("eprinttype") or "").strip().lower()
    if (eprint := (entry.get("eprint") or "").strip()) and prefix == "arxiv":
        csl["number"] = f"arXiv:{eprint}"
        csl.setdefault("URL", f"https://arxiv.org/abs/{eprint}")
        if bib_type == "misc":
            csl["type"] = csl_type = "article"
    if csl_type == "thesis":
        if school := entry.get("school"):
            csl.setdefault("publisher", _strip_braces(school))
        # BibTeX's `type` field names an unusual kind ("Habilitation thesis").
        kind = entry.get("type")
        csl.setdefault("genre", _strip_braces(kind) if kind else _THESIS_GENRE.get(bib_type))
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


def authors_text(csl: dict) -> str:
    """"Family, Given; Family, Given" (organisations by name) for display."""
    names = []
    for a in csl.get("author") or []:
        if not isinstance(a, dict):
            continue
        if a.get("literal"):
            names.append(str(a["literal"]))
        else:
            names.append(", ".join(str(p) for p in (a.get("family"), a.get("given")) if p))
    return "; ".join(n for n in names if n)


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


# Google-Scholar style preprint "venue": "arXiv preprint arXiv:2305.20050".
_ARXIV_CONTAINER = re.compile(r"^\s*arxiv\b", re.IGNORECASE)
# Exported as text (escaped) / verbatim (identifiers and links, which biblatex reads raw).
_CSL_TO_BIB_TEXT = (
    ("publisher", "publisher"),
    ("publisher-place", "address"),
    ("volume", "volume"),
    ("issue", "number"),
    ("page", "pages"),
    ("edition", "edition"),
    ("note", "note"),
    ("abstract", "abstract"),
)
_CSL_TO_BIB_VERBATIM = (("DOI", "doi"), ("URL", "url"), ("ISBN", "isbn"), ("ISSN", "issn"))
# A preprint's "volume"/"issue"/"pages" are not real: BibTeX styles warn about them.
_NOT_FOR_PREPRINTS = {"volume", "number", "pages"}
# arXiv's category as Zotero notes it: "arXiv:2305.15334 [cs.CL]".
_ARXIV_CLASS = re.compile(r"\[([a-z-]+(?:\.[A-Za-z-]+)?)\]")


def _is_preprint(csl: dict) -> bool:
    """CSL "article" is the preprint type; a venue like "arXiv preprint arXiv:..." too."""
    container = csl.get("container-title")
    return csl.get("type") == "article" or (
        isinstance(container, str) and bool(_ARXIV_CONTAINER.match(container))
    )


def csl_to_bibtex_entry(citation_key: str, csl: dict) -> dict:
    preprint = _is_preprint(csl)
    # Preprints are @misc (+ eprint for arXiv), not an @article in a made-up journal.
    bib_type = "misc" if preprint else _CSL_TO_BIBTEX_TYPE.get(csl.get("type", "document"), "misc")
    genre = csl.get("genre") if isinstance(csl.get("genre"), str) else ""
    if bib_type == "phdthesis" and _MASTERS_GENRE.match(genre):
        bib_type = "mastersthesis"
    key = _bib_key(str(citation_key or csl.get("id") or "")) or "ref"
    entry: dict = {"ENTRYTYPE": bib_type, "ID": key}
    if csl.get("title"):
        entry["title"] = _bib_text(csl["title"])
    for role in ("author", "editor"):
        if isinstance(csl.get(role), list) and (names := _csl_names_to_bibtex(csl[role])):
            entry[role] = names
    if csl.get("container-title") and not preprint:
        field = "journal" if bib_type == "article" else "booktitle"
        entry[field] = _bib_text(csl["container-title"])
    for csl_key, bib_key in _CSL_TO_BIB_TEXT:
        if csl.get(csl_key) and not (preprint and bib_key in _NOT_FOR_PREPRINTS):
            entry[bib_key] = _bib_text(csl[csl_key])
    if bib_type == "misc" and csl.get("genre"):
        entry["howpublished"] = _bib_text(csl["genre"])  # e.g. "Preprint"
    if bib_type in _THESIS_GENRE:
        # Theses name their university as `school`; styles ignore `publisher` on them.
        if "publisher" in entry:
            entry["school"] = entry.pop("publisher")
        if genre and genre != _THESIS_GENRE[bib_type] and not _MASTERS_GENRE.match(genre):
            entry["type"] = _bib_text(genre)
    for csl_key, bib_key in _CSL_TO_BIB_VERBATIM:
        if csl.get(csl_key):
            entry[bib_key] = _bib_verbatim(csl[csl_key])
    if preprint and (arxiv_id := extract_arxiv_id(csl)):
        entry["eprint"] = arxiv_id
        entry["archiveprefix"] = "arXiv"
        if isinstance(csl.get("note"), str) and (m := _ARXIV_CLASS.search(csl["note"])):
            entry["primaryclass"] = m.group(1)
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
