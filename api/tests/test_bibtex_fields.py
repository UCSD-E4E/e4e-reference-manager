"""BibTeX export of the fields papers actually need (address, ISBN, notes, arXiv
eprints), and import of the same fields, so a .bib → refman → .bib round trip keeps
them. Pure functions; no DB."""
import pytest

from app.bibtex import build_bibtex, csl_to_bibtex_entry, parse_bibtex


def _export(csl: dict, key: str = "k") -> dict:
    return csl_to_bibtex_entry(key, csl)


def _reparse(csl: dict) -> dict:
    """Export `csl`, then parse the .bib text back; the re-imported entry's CSL."""
    [entry] = parse_bibtex(build_bibtex([("k", csl, None)]))
    return entry.csl_json


# ── fields the exporter used to drop ────────────────────────────────────────────────────


def test_publisher_place_exports_as_address():
    e = _export({"type": "book", "title": "Fish Biology", "publisher": "OUP",
                 "publisher-place": "Oxford"})
    assert e["address"] == "Oxford"


@pytest.mark.parametrize("csl_key,bib_key", [("ISBN", "isbn"), ("ISSN", "issn")])
def test_identifiers_export_verbatim(csl_key, bib_key):
    e = _export({"type": "book", "title": "Vision", csl_key: "978-0-262-51462-0"})
    assert e[bib_key] == "978-0-262-51462-0"


def test_note_and_edition_export():
    e = _export({"type": "book", "title": "T", "note": "Reprint of 1982 ed.", "edition": "2"})
    assert e["note"] == "Reprint of 1982 ed."
    assert e["edition"] == "2"


def test_misc_genre_exports_as_howpublished():
    e = _export({"type": "document", "title": "FishCamera", "genre": "Preprint"})
    assert e["ENTRYTYPE"] == "misc"
    assert e["howpublished"] == "Preprint"


# ── arXiv preprints: @misc with eprint, not an @article with a made-up journal ─────────


@pytest.mark.parametrize(
    "csl",
    [
        # Google-Scholar style, as Uesato/Lightman/Gorilla/ToolLLM are stored
        {"type": "article-journal", "title": "Let's Verify Step by Step",
         "container-title": "arXiv preprint arXiv:2305.20050"},
        # CSL "article" (= preprint) with arXiv's DataCite DOI
        {"type": "article", "title": "Let's Verify Step by Step",
         "DOI": "10.48550/arXiv.2305.20050"},
        # an arxiv.org URL
        {"type": "article", "title": "Let's Verify Step by Step",
         "URL": "https://arxiv.org/abs/2305.20050v2"},
    ],
)
def test_arxiv_preprints_export_as_misc_with_eprint(csl):
    e = _export(csl)
    assert e["ENTRYTYPE"] == "misc"
    assert e["eprint"] == "2305.20050"
    assert e["archiveprefix"] == "arXiv"
    assert "journal" not in e and "volume" not in e and "pages" not in e


def test_a_published_paper_with_an_arxiv_link_stays_an_article():
    """A journal article whose record also links its arXiv version is not a preprint."""
    e = _export({"type": "article-journal", "title": "Attention Is All You Need",
                 "container-title": "NeurIPS", "volume": "30",
                 "URL": "https://arxiv.org/abs/1706.03762"})
    assert e["ENTRYTYPE"] == "article"
    assert e["journal"] == "NeurIPS"
    assert "eprint" not in e


def test_preprint_without_an_arxiv_id_is_still_misc():
    e = _export({"type": "article", "title": "Some Preprint", "publisher": "OSF"})
    assert e["ENTRYTYPE"] == "misc"
    assert "eprint" not in e


# ── import reads the same fields, so a round trip keeps them ───────────────────────────


def test_import_reads_address_isbn_issn_note_edition_howpublished():
    [entry] = parse_bibtex(
        """@book{jennings2001,
          title = {Marine Fisheries Ecology}, publisher = {Blackwell},
          address = {Oxford}, isbn = {978-0-632-05098-7}, issn = {1234-5678},
          note = {Paperback}, edition = {2}, year = {2001}}"""
    )
    csl = entry.csl_json
    assert csl["publisher-place"] == "Oxford"
    assert csl["ISBN"] == "978-0-632-05098-7"
    assert csl["ISSN"] == "1234-5678"
    assert csl["note"] == "Paperback"
    assert csl["edition"] == "2"
    fsl = parse_bibtex("@misc{fsl, title = {FishCamera}, howpublished = {Preprint}}")[0]
    assert fsl.csl_json["genre"] == "Preprint"


@pytest.mark.parametrize("field", ["archiveprefix = {arXiv}", "eprinttype = {arxiv}"])
def test_import_reads_arxiv_eprints(field):
    [entry] = parse_bibtex(
        f"@misc{{lightman2023, title = {{Let's Verify Step by Step}}, "
        f"eprint = {{2305.20050}}, {field}, year = {{2023}}}}"
    )
    csl = entry.csl_json
    assert csl["type"] == "article"  # CSL's preprint type
    assert csl["number"] == "arXiv:2305.20050"
    assert csl["URL"] == "https://arxiv.org/abs/2305.20050"


def test_round_trip_keeps_every_field():
    book = {"type": "book", "title": "Vision", "author": [{"family": "Marr", "given": "David"}],
            "publisher": "MIT Press", "publisher-place": "Cambridge, MA",
            "ISBN": "978-0-262-51462-0", "note": "Reissued 2010", "edition": "2",
            "issued": {"date-parts": [[1982]]}}
    back = _reparse(book)
    for key in ("publisher-place", "ISBN", "note", "edition", "publisher", "title"):
        assert back[key] == book[key]

    preprint = {"type": "article", "title": "Let's Verify Step by Step",
                "DOI": "10.48550/arXiv.2305.20050", "issued": {"date-parts": [[2023]]}}
    back = _reparse(preprint)
    assert back["type"] == "article"
    assert back["number"] == "arXiv:2305.20050"
    assert back["DOI"] == "10.48550/arXiv.2305.20050"


def test_arxiv_category_in_the_note_exports_as_primaryclass():
    """Zotero stores arXiv imports with a note like "arXiv:2305.15334 [cs.CL]"."""
    e = _export({"type": "article", "title": "Gorilla", "DOI": "10.48550/arXiv.2305.15334",
                 "note": "arXiv:2305.15334 [cs.CL]"})
    assert e["eprint"] == "2305.15334"
    assert e["primaryclass"] == "cs.CL"
