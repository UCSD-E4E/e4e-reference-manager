"""Pure unit tests (no DB, no network) for bibtex / ingest / grobid logic."""
import pytest

from app.bibtex import build_bibtex, parse_bibtex, year_from_csl
from app.grobid import extract_header_csl, parse_tei_header
from app.ingest import gen_citation_key, normalize_doi

SAMPLE_BIB = """
@article{smith2020ml,
  title = {A Study of Machine Learning},
  author = {Smith, Jane and Doe, John A.},
  journal = {Journal of AI},
  year = {2020},
  pages = {100--115},
  doi = {10.1000/xyz123}
}
@inproceedings{lee2019vision,
  title = {Vision Systems},
  author = {Lee, Kim},
  booktitle = {Proc. CVPR},
  year = {2019}
}
"""


def test_parse_bibtex_preserves_keys_and_maps_csl():
    entries = parse_bibtex(SAMPLE_BIB)
    assert [e.citation_key for e in entries] == ["smith2020ml", "lee2019vision"]
    art = entries[0]
    assert art.csl_type == "article-journal"
    assert art.csl_json["title"] == "A Study of Machine Learning"
    assert art.csl_json["author"] == [
        {"family": "Smith", "given": "Jane"},
        {"family": "Doe", "given": "John A."},
    ]
    assert year_from_csl(art.csl_json) == 2020
    assert entries[1].csl_type == "paper-conference"


def test_parse_bibtex_empty():
    assert parse_bibtex("not bibtex at all") == []


def test_build_bibtex_roundtrip_preserves_raw_keys():
    entries = parse_bibtex(SAMPLE_BIB)
    out = build_bibtex([(e.citation_key, e.csl_json, e.raw_bibtex) for e in entries])
    assert "@article{smith2020ml," in out
    assert "@inproceedings{lee2019vision," in out


def test_build_bibtex_from_csl_when_no_raw():
    entries = parse_bibtex(SAMPLE_BIB)
    out = build_bibtex([(e.citation_key, e.csl_json, None) for e in entries])
    assert "smith2020ml" in out and "Machine Learning" in out


def test_gen_citation_key():
    csl = {
        "title": "Deep Learning for Coral Reefs",
        "author": [{"family": "Smith", "given": "Jane"}],
        "issued": {"date-parts": [[2021]]},
    }
    assert gen_citation_key(csl) == "smith2021deep"
    assert gen_citation_key({}) == "ref"


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/XyZ") == "10.1000/xyz"
    assert normalize_doi("10.1/A") == "10.1/a"
    assert normalize_doi(None) is None
    assert normalize_doi("") is None


def test_grobid_parse_tei_header():
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader>
      <fileDesc><titleStmt><title>Deep Learning for Coral Reefs</title></titleStmt>
      <sourceDesc><biblStruct><analytic>
        <author><persName><forename type="first">Jane</forename><surname>Smith</surname></persName></author>
        <author><persName><surname>Doe</surname></persName></author>
        <idno type="DOI">10.1000/CORAL.2021</idno>
      </analytic></biblStruct></sourceDesc></fileDesc>
      <profileDesc><abstract><p>We apply deep learning to reefs.</p></abstract></profileDesc>
      <publicationStmt><date type="published" when="2021-06-01">2021</date></publicationStmt>
    </teiHeader></TEI>"""
    csl = parse_tei_header(xml)
    assert csl["title"] == "Deep Learning for Coral Reefs"
    assert csl["author"] == [{"family": "Smith", "given": "Jane"}, {"family": "Doe"}]
    assert csl["DOI"] == "10.1000/coral.2021"
    assert csl["abstract"].startswith("We apply deep learning")
    assert year_from_csl(csl) == 2021


_TEI = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc>
<titleStmt><title>Sample Paper</title></titleStmt>
<sourceDesc><biblStruct><analytic>
<author><persName><forename type="first">A</forename><surname>Smith</surname></persName></author>
</analytic></biblStruct></sourceDesc></fileDesc></teiHeader></TEI>"""


async def test_extract_header_requests_tei_not_bibtex(monkeypatch):
    """Regression: GROBID returns BibTeX unless we ask for TEI (Accept: application/xml)."""
    captured = {}

    class FakeResp:
        status_code = 200
        text = _TEI

    async def fake_post(self, url, **kwargs):
        captured["headers"] = kwargs.get("headers") or {}
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    csl = await extract_header_csl(b"%PDF-1.4 fake")
    assert captured["headers"].get("Accept") == "application/xml"
    assert csl["title"] == "Sample Paper"
    assert csl["author"][0]["family"] == "Smith"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "{DSL} Modems with Analog Voice Band Support",
            "DSL Modems with Analog Voice Band Support",
        ),
        ("{{A Fully Braced Title}}", "A Fully Braced Title"),
        ("Coral {Reef} {CNN}s for {AUV}s", "Coral Reef CNNs for AUVs"),
        ("Plain Title", "Plain Title"),
        (r"Costs in 100\{\} units", r"Costs in 100\{\} units"),  # escaped braces stay
    ],
)
def test_bibtex_title_protective_braces_are_removed(raw, expected):
    [entry] = parse_bibtex(f"@article{{k, title = {{{raw}}}, journal = {{{{IEEE}} Sensors}}}}")
    assert entry.csl_json["title"] == expected
    assert entry.csl_json["container-title"] == "IEEE Sensors"


# ---------- hostile .bib input ----------


@pytest.mark.parametrize("year, kept", [("2020", 2020), ("1", 1), ("9999", 9999), ("0", None),
                                        ("10000", None), ("99999999999", None), ("-3", None)])
def test_bibtex_years_outside_1_to_9999_are_dropped(year, kept):
    [entry] = parse_bibtex(f"@misc{{k, title={{T}}, year={{{year}}}}}")
    got = entry.csl_json.get("issued", {}).get("date-parts", [[None]])[0][0]
    assert got == kept
    assert year_from_csl({"issued": {"date-parts": [[int(year)]]}}) == kept


def test_year_from_csl_rejects_out_of_range_values():
    assert year_from_csl({"issued": {"date-parts": [[10**12]]}}) is None
    assert year_from_csl({"issued": {"date-parts": [["1999"]]}}) == 1999


def test_bibtex_nul_bytes_are_stripped():
    [entry] = parse_bibtex("@misc{k\x00ey, title={Nul\x00 title}, note={a\x00b}}")
    assert "\x00" not in entry.raw_bibtex
    assert "\x00" not in repr(entry.csl_json) and "\x00" not in entry.citation_key
    assert entry.csl_json["title"] == "Nul title"


# ---------- generated BibTeX export ----------


def _export_one(csl, key="k"):
    return build_bibtex([(key, {"type": "article-journal", **csl}, None)])


def _reparse(out):
    return parse_bibtex(out)


def test_literal_author_names_are_exported_braced():
    out = _export_one({"title": "Report", "author": [{"literal": "World Health Organization"},
                                                      {"family": "Ng", "given": "A"}]})
    assert "author = {{World Health Organization} and Ng, A}" in out
    [entry] = _reparse(out)
    assert entry.csl_json["author"][0] == {"literal": "World Health Organization"}


def test_latex_specials_are_escaped_in_generated_entries():
    out = _export_one({"title": "R&D at 50% for $5 #1 my_var"})
    assert r"R\&D at 50\% for \$5 \#1 my\_var" in out
    # already-escaped specials aren't escaped twice
    assert r"A \& B" in _export_one({"title": r"A \& B"})


def test_urls_and_dois_are_not_latex_escaped():
    out = _export_one({"title": "T", "URL": "https://x.example/a_b?c=1&d=2",
                       "DOI": "10.1/a_b"})
    assert "url = {https://x.example/a_b?c=1&d=2}" in out
    assert "doi = {10.1/a_b}" in out


@pytest.mark.parametrize("title", ["Unbalanced } close", "Unclosed { open", "}{", "a}}b{{c"])
def test_unbalanced_braces_cannot_swallow_the_next_entry(title):
    out = build_bibtex(
        [
            ("bad", {"type": "article-journal", "title": title}, None),
            ("good", {"type": "article-journal", "title": "Still here"}, None),
        ]
    )
    entries = _reparse(out)
    assert [e.citation_key for e in entries] == ["bad", "good"]
    assert entries[1].csl_json["title"] == "Still here"


def test_odd_csl_values_do_not_crash_export():
    out = _export_one({"title": 42, "author": ["not a dict", {"family": "Ng"}],
                       "page": ["1", "2"]}, key="we{ird} key,")
    [entry] = _reparse(out)
    assert entry.csl_json["author"] == [{"family": "Ng"}]
