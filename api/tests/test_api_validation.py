"""Phase 5: anti-hallucination validation against canonical registrars.

For items with a DOI we hit Crossref, then DataCite; arXiv IDs hit the arXiv API; bare
metadata falls back to a Crossref title/author search. The registrar's title is compared
to the item's to catch fabricated DOIs that happen to resolve to a *different* real
paper. All HTTP is monkeypatched in tests."""
import pytest

import app.validation as v
from app.validation import (
    normalize_title,
    parse_arxiv_atom,
    parse_crossref_work,
    title_similarity,
)

REAL_CROSSREF = v.crossref_lookup  # captured before any test patches them
REAL_DATACITE = getattr(v, "datacite_lookup", None)


# ---------- pure helpers ----------

def test_normalize_title_strips_punct_and_lowercases():
    assert normalize_title("  Attention Is All You Need!  ") == "attention is all you need"
    assert normalize_title("Coral-reef genomics (2nd ed.)") == "coral reef genomics 2nd ed"


def test_title_similarity_identical_is_one_disjoint_is_zero():
    assert title_similarity("Attention is all you need", "Attention is all you need") == 1.0
    assert title_similarity("Coral reefs", "Quantum chromodynamics") == 0.0


def test_title_similarity_partial_overlap_above_threshold():
    # A reasonable rewording should still cross the match threshold (0.6).
    assert title_similarity("Deep learning for coral reefs", "Deep Learning, Coral Reefs") >= 0.6


def test_parse_crossref_work_extracts_title_and_doi():
    js = {
        "message": {
            "title": ["Attention Is All You Need"],
            "DOI": "10.48550/arXiv.1706.03762",
            "issued": {"date-parts": [[2017]]},
            "author": [{"family": "Vaswani", "given": "Ashish"}],
        }
    }
    out = parse_crossref_work(js)
    assert out["title"] == "Attention Is All You Need"
    assert out["DOI"] == "10.48550/arxiv.1706.03762"
    assert out["year"] == 2017


def test_parse_arxiv_atom_extracts_title():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1706.03762v5</id>
        <title>Attention Is All You Need</title>
        <published>2017-06-12T00:00:00Z</published>
      </entry>
    </feed>"""
    out = parse_arxiv_atom(xml)
    assert out is not None
    assert out["title"] == "Attention Is All You Need"
    assert out["year"] == 2017


# ---------- validate_item_csl orchestrator (HTTP monkeypatched) ----------

def _stub_crossref(monkeypatch, doi_to_work):
    """Make crossref_lookup return doi_to_work.get(doi)."""

    async def fake(doi):
        return doi_to_work.get((doi or "").lower())

    monkeypatch.setattr(v, "crossref_lookup", fake)


def _stub_arxiv(monkeypatch, mapping):
    async def fake(arxiv_id):
        return mapping.get(arxiv_id)

    monkeypatch.setattr(v, "arxiv_lookup", fake)


def _stub_crossref_search(monkeypatch, by_title):
    async def fake(title, authors=None):
        return by_title.get(normalize_title(title or ""))

    monkeypatch.setattr(v, "crossref_search", fake)


async def test_doi_resolves_with_matching_title_is_verified(monkeypatch):
    _stub_crossref(
        monkeypatch,
        {"10.1/x": {"title": "Attention Is All You Need", "DOI": "10.1/x", "year": 2017}},
    )
    out = await v.validate_item_csl(
        {"DOI": "10.1/X", "title": "Attention Is All You Need", "issued": {"date-parts": [[2017]]}}
    )
    assert out["status"] == "verified"
    assert out["source"] == "crossref"
    assert out["title_similarity"] >= 0.99


async def test_doi_resolves_with_mismatched_title_is_metadata_mismatch(monkeypatch):
    _stub_crossref(
        monkeypatch,
        {"10.1/x": {"title": "A Completely Different Paper", "DOI": "10.1/x", "year": 1999}},
    )
    out = await v.validate_item_csl({"DOI": "10.1/x", "title": "Attention Is All You Need"})
    assert out["status"] == "metadata_mismatch"
    assert out["matched_title"] == "A Completely Different Paper"


async def test_doi_404_is_not_found(monkeypatch):
    _stub_crossref(monkeypatch, {})  # nothing resolves
    out = await v.validate_item_csl({"DOI": "10.fake/missing", "title": "Anything"})
    assert out["status"] == "not_found"
    assert out["source"] == "crossref"


async def test_arxiv_id_resolves(monkeypatch):
    _stub_arxiv(monkeypatch, {"1706.03762": {"title": "Attention Is All You Need", "year": 2017}})
    # The CSL extension `note` carries the arXiv id (commonly stored there).
    out = await v.validate_item_csl(
        {"title": "Attention Is All You Need", "note": "arXiv:1706.03762"}
    )
    assert out["status"] == "verified"
    assert out["source"] == "arxiv"


async def test_no_doi_falls_back_to_title_search_and_verifies(monkeypatch):
    _stub_crossref(monkeypatch, {})
    _stub_crossref_search(
        monkeypatch,
        {normalize_title("Coral reefs and bleaching"): {"title": "Coral Reefs and Bleaching"}},
    )
    out = await v.validate_item_csl({"title": "Coral reefs and bleaching"})
    assert out["status"] == "verified"
    assert out["source"] == "crossref"


async def test_no_identifier_and_no_title_match_is_unverifiable(monkeypatch):
    _stub_crossref(monkeypatch, {})
    _stub_crossref_search(monkeypatch, {})
    out = await v.validate_item_csl({"title": "Some made up paper that does not exist"})
    assert out["status"] == "unverifiable"


# ---------- endpoints ----------

async def _item(client, library, **csl):
    csl.setdefault("type", "document")
    return (
        await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    ).json()


async def test_validate_item_persists_and_appears_on_item(client, library, monkeypatch):
    _stub_crossref(
        monkeypatch, {"10.1/x": {"title": "Real Paper", "DOI": "10.1/x", "year": 2020}}
    )
    it = await _item(client, library, title="Real Paper", DOI="10.1/x")

    r = await client.post(f"/items/{it['id']}/validate")
    assert r.status_code == 200
    assert r.json()["status"] == "verified"
    assert r.json()["source"] == "crossref"

    # The verdict is stored on the item and visible via GET /items/{id}.
    got = await client.get(f"/items/{it['id']}")
    assert got.json()["validation"]["status"] == "verified"


async def test_validate_library_batch_returns_summary(client, library, monkeypatch):
    _stub_crossref(
        monkeypatch,
        {
            "10.1/real": {"title": "Real Paper", "DOI": "10.1/real"},
            "10.1/wrong": {"title": "Different Paper", "DOI": "10.1/wrong"},
        },
    )
    _stub_crossref_search(monkeypatch, {})  # title search never finds anything
    await _item(client, library, title="Real Paper", DOI="10.1/real")  # verified
    await _item(client, library, title="Claimed Paper", DOI="10.1/wrong")  # mismatch
    await _item(client, library, title="Anything", DOI="10.1/missing")  # not_found
    await _item(client, library, title="Some untitled doc")  # unverifiable (no DOI)

    r = await client.post(f"/libraries/{library}/validate")
    assert r.status_code == 200
    s = r.json()
    assert s["checked"] == 4
    assert s["verified"] == 1
    assert s["metadata_mismatch"] == 1
    assert s["not_found"] == 1
    assert s["unverifiable"] == 1


# ---------- DOIs written as URLs or with URL-special characters ----------


@pytest.mark.parametrize(
    "written", ["https://doi.org/10.1/X", "http://dx.doi.org/10.1/x", " 10.1/X ", "doi:10.1/x"]
)
async def test_doi_written_as_a_url_is_looked_up_by_the_bare_doi(monkeypatch, written):
    _stub_crossref(monkeypatch, {"10.1/x": {"title": "Reef Paper", "DOI": "10.1/x"}})
    out = await v.validate_item_csl({"DOI": written, "title": "Reef Paper"})
    assert out["status"] == "verified"


async def test_crossref_lookup_quotes_the_doi_in_the_path(monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"message": {"title": ["T"], "DOI": "10.1/a#b?c"}})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        v.httpx, "AsyncClient", lambda **k: real(transport=httpx.MockTransport(handler), **k)
    )
    assert (await v.crossref_lookup("10.1/a#b?c"))["title"] == "T"
    (url,) = seen
    assert url.raw_path == b"/works/10.1/a%23b%3Fc"
    assert not url.query and not url.fragment


# ---------- DOIs Crossref doesn't hold (DataCite: arXiv, Zenodo, ...) ----------

SAM3 = "SAM 3: Segment Anything with Concepts"


def _stub_datacite(monkeypatch, doi_to_work):
    async def fake(doi):
        return doi_to_work.get((doi or "").lower())

    monkeypatch.setattr(v, "datacite_lookup", fake)


async def test_arxiv_doi_is_verified_through_datacite(monkeypatch):
    """arXiv's 10.48550 DOIs are registered with DataCite, not Crossref: a real arXiv
    paper must not be reported as not_found (i.e. possibly fabricated)."""
    _stub_crossref(monkeypatch, {})
    _stub_datacite(monkeypatch, {"10.48550/arxiv.2511.16719": {"title": SAM3}})
    out = await v.validate_item_csl({"DOI": "10.48550/arXiv.2511.16719", "title": SAM3})
    assert out["status"] == "verified"
    assert out["source"] == "datacite"
    assert out["matched_title"] == SAM3


async def test_datacite_doi_with_a_different_title_is_a_mismatch(monkeypatch):
    _stub_crossref(monkeypatch, {})
    _stub_datacite(monkeypatch, {"10.5281/zenodo.1": {"title": "A Coral Dataset"}})
    out = await v.validate_item_csl({"DOI": "10.5281/zenodo.1", "title": "Fish Lengths"})
    assert out["status"] == "metadata_mismatch"
    assert out["source"] == "datacite"


async def test_doi_in_neither_registry_is_not_found(monkeypatch):
    _stub_crossref(monkeypatch, {})
    _stub_datacite(monkeypatch, {})
    out = await v.validate_item_csl({"DOI": "10.fake/missing", "title": "Anything"})
    assert out["status"] == "not_found"
    assert "Crossref" in out["notes"] and "DataCite" in out["notes"]


@pytest.mark.parametrize("down", ["crossref", "datacite"])
async def test_an_unreachable_registry_is_unverifiable_not_not_found(monkeypatch, down):
    """An outage says nothing about whether the paper exists."""

    async def unavailable(doi):
        raise v.RegistryUnavailable("timed out")

    _stub_crossref(monkeypatch, {})
    _stub_datacite(monkeypatch, {})
    monkeypatch.setattr(v, f"{down}_lookup", unavailable)
    out = await v.validate_item_csl({"DOI": "10.1/x", "title": "Reef Paper"})
    assert out["status"] == "unverifiable"


def _mock_http(monkeypatch, handler):
    import httpx

    real = httpx.AsyncClient
    monkeypatch.setattr(
        v.httpx, "AsyncClient", lambda **k: real(transport=httpx.MockTransport(handler), **k)
    )


@pytest.mark.parametrize(
    "lookup,url", [(REAL_CROSSREF, "api.crossref.org"), (REAL_DATACITE, "api.datacite.org")]
)
async def test_lookups_tell_missing_from_unavailable(monkeypatch, lookup, url):
    import httpx

    status = {"code": 404}

    def handler(request):
        assert request.url.host == url
        if status["code"] == "boom":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(status["code"], json={})

    _mock_http(monkeypatch, handler)
    assert await lookup("10.1/x") is None  # 404: no such DOI
    for status["code"] in (500, 503, "boom"):
        with pytest.raises(v.RegistryUnavailable):
            await lookup("10.1/x")


async def test_datacite_lookup_reads_the_title(monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(request.url)
        body = {"data": {"attributes": {"titles": [{"title": SAM3}], "publicationYear": 2025}}}
        return httpx.Response(200, json=body)

    _mock_http(monkeypatch, handler)
    work = await REAL_DATACITE("10.48550/arXiv.2511.16719")
    assert work == {"title": SAM3, "DOI": "10.48550/arXiv.2511.16719", "year": 2025}
    assert seen[0].raw_path == b"/dois/10.48550/arXiv.2511.16719"
