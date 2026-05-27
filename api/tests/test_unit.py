"""Pure unit tests (no DB, no network) for bibtex / ingest / grobid logic."""
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
