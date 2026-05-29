"""Phase 6: extract a PDF's bibliography (GROBID processReferences) and validate every
citation in it against Crossref/arXiv. Both the per-item endpoint (uses an existing PDF
attachment) and a standalone upload endpoint are covered."""
import app.grobid as grobid_mod
import app.validation as v_mod
from app.grobid import parse_tei_references

_TEI = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><div><listBibl>
<biblStruct xml:id="b0">
  <analytic>
    <title level="a">Attention is all you need</title>
    <author><persName><forename type="first">Ashish</forename><surname>Vaswani</surname></persName></author>
    <idno type="DOI">10.1/X</idno>
  </analytic>
  <monogr><imprint><date type="published" when="2017"/></imprint></monogr>
  <note type="raw_reference">Vaswani A et al. 2017. Attention is all you need...</note>
</biblStruct>
<biblStruct xml:id="b1">
  <monogr>
    <title level="m">A Standalone Monograph Title</title>
    <imprint><date type="published" when="2020"/></imprint>
  </monogr>
</biblStruct>
</listBibl></div></back></text></TEI>"""


def test_parse_tei_references_extracts_each_citation():
    refs = parse_tei_references(_TEI)
    assert len(refs) == 2
    first = refs[0]
    assert first["title"] == "Attention is all you need"
    assert first["DOI"] == "10.1/x"
    assert first["author"][0]["family"] == "Vaswani"
    assert first["author"][0]["given"] == "Ashish"
    assert first["issued"]["date-parts"][0][0] == 2017
    assert "Vaswani A" in first["_raw"]
    # Falls back to <monogr><title> when there's no analytic title.
    assert refs[1]["title"] == "A Standalone Monograph Title"


def test_parse_tei_references_empty_or_missing_listbibl_is_empty():
    assert parse_tei_references("<TEI xmlns='http://www.tei-c.org/ns/1.0'></TEI>") == []


# ---------- endpoint tests ----------


async def _item_with_pdf(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = (
        await client.post(
            f"/libraries/{library}/items",
            json={"csl_json": {"type": "article-journal", "title": "Host Paper"}},
        )
    ).json()
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 201
    return item, r.json()["id"]


def _stub_grobid_refs(monkeypatch, refs):
    async def fake_extract(_data):
        return refs

    # Patched on the grobid module so it applies wherever it's called from.
    monkeypatch.setattr(grobid_mod, "extract_references", fake_extract)
    # The per-item endpoint pulls the PDF from storage; stub that too.
    monkeypatch.setattr(
        "app.routers.validate.download_bytes", lambda *a, **k: b"%PDF-1.4 fake"
    )


def _stub_crossref(monkeypatch, by_doi):
    async def fake_lookup(doi):
        return by_doi.get((doi or "").lower())

    async def fake_search(_title, _authors=None):
        return None

    monkeypatch.setattr(v_mod, "crossref_lookup", fake_lookup)
    monkeypatch.setattr(v_mod, "crossref_search", fake_search)


async def test_validate_item_references_returns_summary_and_per_ref(
    client, library, monkeypatch
):
    item, _att = await _item_with_pdf(client, library, monkeypatch)
    refs = [
        {"title": "Real Paper", "DOI": "10.1/real", "_raw": "Real Paper. 2020."},
        {"title": "Claimed Title", "DOI": "10.1/wrong", "_raw": "Claimed Title. 2021."},
        {"title": "Hallucinated", "DOI": "10.1/missing", "_raw": "Hallucinated. 2024."},
        {"title": "Bare title only", "_raw": "Some authors. 2019. Bare title only."},
    ]
    _stub_grobid_refs(monkeypatch, refs)
    _stub_crossref(
        monkeypatch,
        {
            "10.1/real": {"title": "Real Paper", "DOI": "10.1/real"},
            "10.1/wrong": {"title": "Totally Different Paper", "DOI": "10.1/wrong"},
        },
    )

    r = await client.post(f"/items/{item['id']}/validate-references")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == {
        "total": 4,
        "verified": 1,
        "metadata_mismatch": 1,
        "not_found": 1,
        "unverifiable": 1,
    }
    assert len(body["references"]) == 4
    assert body["references"][0]["verdict"]["status"] == "verified"
    assert body["references"][0]["cited_text"].startswith("Real Paper")
    assert body["references"][1]["verdict"]["status"] == "metadata_mismatch"
    assert body["references"][2]["verdict"]["status"] == "not_found"
    assert body["references"][3]["verdict"]["status"] == "unverifiable"


async def test_validate_item_references_requires_a_pdf(client, library):
    item = (
        await client.post(
            f"/libraries/{library}/items",
            json={"csl_json": {"type": "document", "title": "No PDF here"}},
        )
    ).json()
    r = await client.post(f"/items/{item['id']}/validate-references")
    assert r.status_code == 400


async def test_pdf_validate_standalone_upload(client, alice, monkeypatch):
    refs = [
        {"title": "Real Paper", "DOI": "10.1/r", "_raw": "Real Paper."},
        {"title": "Made up", "DOI": "10.1/missing", "_raw": "Made up."},
    ]
    _stub_grobid_refs(monkeypatch, refs)
    _stub_crossref(monkeypatch, {"10.1/r": {"title": "Real Paper", "DOI": "10.1/r"}})

    r = await client.post(
        "/pdf-validate", files={"file": ("draft.pdf", b"%PDF-1.4 fake", "application/pdf")}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total"] == 2
    assert body["summary"]["verified"] == 1
    assert body["summary"]["not_found"] == 1


async def test_pdf_validate_empty_file_rejected(client, alice):
    r = await client.post(
        "/pdf-validate", files={"file": ("empty.pdf", b"", "application/pdf")}
    )
    assert r.status_code == 400
