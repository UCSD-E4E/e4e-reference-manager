"""Phase 5: anti-hallucination validation against canonical registrars.

For items with a DOI we hit Crossref; arXiv IDs hit the arXiv API; bare metadata falls
back to a Crossref title/author search. The registrar's title is compared to the item's
to catch fabricated DOIs that happen to resolve to a *different* real paper. All HTTP
is monkeypatched in tests."""
import app.validation as v
from app.validation import (
    normalize_title,
    parse_arxiv_atom,
    parse_crossref_work,
    title_similarity,
)


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
