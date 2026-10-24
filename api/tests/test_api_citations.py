"""Citation graph: which papers in a library cite which. Links come from OpenAlex
(reference lists by DOI) and from attached PDFs (GROBID's reference extraction), are
matched to the library's own papers (DOI, OpenAlex id, or title + year), stored, and
served as nodes + edges. No network: OpenAlex is an httpx.MockTransport and GROBID is
monkeypatched."""
import json

import httpx
import pytest
from sqlalchemy import select

import app.grobid as grobid
import app.openalex as openalex
from app.models import Attachment, Item


def _work(oa_id, doi, refs=()):
    return {
        "id": f"https://openalex.org/{oa_id}",
        "doi": f"https://doi.org/{doi}",
        "referenced_works": [f"https://openalex.org/{r}" for r in refs],
    }


@pytest.fixture
def oa(monkeypatch):
    """A fake OpenAlex: fill `works` (doi -> work); `calls` records request URLs."""
    state = {"works": {}, "calls": [], "fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.url)
        if state["fail"]:
            return httpx.Response(503)
        flt = request.url.params["filter"]
        assert flt.startswith("doi:")
        wanted = flt[4:].split("|")
        results = [state["works"][d] for d in wanted if d in state["works"]]
        return httpx.Response(200, json={"results": results})

    monkeypatch.setattr(openalex, "_transport", lambda: httpx.MockTransport(handler))
    return state


_STORED: dict[str, bytes] = {}  # storage_key -> PDF bytes (fake object storage)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No Ollama (embeddings on item create) and an in-memory object store."""
    _STORED.clear()

    async def no_embedding(csl):
        return None

    monkeypatch.setattr("app.embeddings.embed_item_csl", no_embedding)
    monkeypatch.setattr("app.routers.citations.download_bytes", _STORED.__getitem__)


@pytest.fixture
def pdf_refs(monkeypatch):
    """Fake GROBID: `refs` maps PDF bytes -> extracted reference CSL list."""
    state = {"refs": {}, "calls": 0}

    async def fake(pdf: bytes) -> list[dict]:
        state["calls"] += 1
        return state["refs"].get(pdf, [])

    monkeypatch.setattr(grobid, "extract_references_strict", fake)
    return state


async def _item(client, library, key, title, year=2020, **csl):
    r = await client.post(
        f"/libraries/{library}/items",
        json={"citation_key": key, "csl_json": {"type": "article-journal", "title": title,
                                                "issued": {"date-parts": [[year]]}, **csl}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _attach_pdf(db, item_id, data: bytes):
    _STORED[f"x/{item_id}"] = data
    db.add(Attachment(item_id=item_id, filename="p.pdf", content_type="application/pdf",
                      size=len(data), sha256=data.hex()[:64].ljust(64, "0"),
                      storage_key=f"x/{item_id}", text=""))
    await db.commit()


async def _build(client, library, **params):
    """Run the batched build to completion; return the last batch's summary."""
    after = None
    while True:
        q = {**params, **({"after": after} if after else {})}
        r = await client.post(f"/libraries/{library}/citations/build", params=q)
        assert r.status_code == 200, r.text
        out = r.json()
        if out["next_after"] is None:
            return out
        after = out["next_after"]


async def _graph(client, library):
    r = await client.get(f"/libraries/{library}/citations")
    assert r.status_code == 200, r.text
    return r.json()


def _edges(graph) -> set[tuple[str, str]]:
    keys = {n["id"]: n["citation_key"] for n in graph["nodes"]}
    return {(keys[e["source"]], keys[e["target"]]) for e in graph["edges"]}


# ── building ─────────────────────────────────────────────────────────────────────────


async def test_openalex_links_papers_by_doi(client, library, oa, pdf_refs, db):
    await _item(client, library, "a", "Paper A", DOI="10.1/A")
    await _item(client, library, "b", "Paper B", DOI="10.1/b")
    await _item(client, library, "c", "Paper C", DOI="10.1/c")
    oa["works"] = {
        "10.1/a": _work("W1", "10.1/a", refs=["W2", "W999"]),  # W999: not in the library
        "10.1/b": _work("W2", "10.1/b", refs=["W3"]),
        "10.1/c": _work("W3", "10.1/c"),
    }
    await _build(client, library)
    graph = await _graph(client, library)
    assert _edges(graph) == {("a", "b"), ("b", "c")}
    assert {e["sources"][0] for e in graph["edges"]} == {"openalex"}
    ids = (await db.execute(select(Item.openalex_id).order_by(Item.citation_key))).scalars().all()
    assert ids == ["W1", "W2", "W3"]
    # Batched lookups: a handful of requests, not one per paper.
    assert len(oa["calls"]) <= 4


async def test_pdf_references_link_papers_without_dois(client, library, oa, pdf_refs, db):
    """Your own drafts have no DOI but cite library papers in their PDF."""
    draft = await _item(client, library, "fsl", "FishCamera", 2026)
    await _item(client, library, "doi_paper", "Stereo Fish", 2019, DOI="10.1/stereo")
    await _item(client, library, "title_paper", "Laser Range Finding for Fish", 2021)
    await _attach_pdf(db, draft, b"%PDF-draft")
    pdf_refs["refs"][b"%PDF-draft"] = [
        {"title": "something else", "DOI": "10.1/STEREO"},  # matched by DOI
        {"title": "Laser range-finding for fish!", "issued": {"date-parts": [[2021]]}},
        {"title": "FishCamera", "issued": {"date-parts": [[2026]]}},  # itself: ignored
        {"title": "Not In The Library", "DOI": "10.9/elsewhere"},
    ]
    await _build(client, library)
    graph = await _graph(client, library)
    assert _edges(graph) == {("fsl", "doi_paper"), ("fsl", "title_paper")}
    assert {tuple(e["sources"]) for e in graph["edges"]} == {("pdf",)}


async def test_both_sources_merge_into_one_edge(client, library, oa, pdf_refs, db):
    a = await _item(client, library, "a", "Paper A", DOI="10.1/a")
    await _item(client, library, "b", "Paper B", DOI="10.1/b")
    await _attach_pdf(db, a, b"%PDF-a")
    pdf_refs["refs"][b"%PDF-a"] = [{"title": "x", "DOI": "10.1/b"}]
    oa["works"] = {"10.1/a": _work("W1", "10.1/a", ["W2"]), "10.1/b": _work("W2", "10.1/b")}
    await _build(client, library)
    [edge] = (await _graph(client, library))["edges"]
    assert sorted(edge["sources"]) == ["openalex", "pdf"]


async def test_rebuild_replaces_old_links(client, library, oa, pdf_refs):
    await _item(client, library, "a", "Paper A", DOI="10.1/a")
    await _item(client, library, "b", "Paper B", DOI="10.1/b")
    oa["works"] = {"10.1/a": _work("W1", "10.1/a", ["W2"]), "10.1/b": _work("W2", "10.1/b")}
    await _build(client, library)
    assert _edges(await _graph(client, library)) == {("a", "b")}
    oa["works"]["10.1/a"] = _work("W1", "10.1/a", [])
    await _build(client, library)
    assert _edges(await _graph(client, library)) == set()


async def test_pdf_references_are_extracted_once(client, library, oa, pdf_refs, db):
    a = await _item(client, library, "a", "Paper A")
    await _item(client, library, "b", "Paper B", 2019)
    await _attach_pdf(db, a, b"%PDF-a")
    pdf_refs["refs"][b"%PDF-a"] = [{"title": "Paper B", "issued": {"date-parts": [[2019]]}}]
    await _build(client, library)
    await _build(client, library)
    assert pdf_refs["calls"] == 1  # cached on the attachment
    assert _edges(await _graph(client, library)) == {("a", "b")}


async def test_a_failed_lookup_keeps_existing_links(client, library, oa, pdf_refs):
    await _item(client, library, "a", "Paper A", DOI="10.1/a")
    await _item(client, library, "b", "Paper B", DOI="10.1/b")
    oa["works"] = {"10.1/a": _work("W1", "10.1/a", ["W2"]), "10.1/b": _work("W2", "10.1/b")}
    await _build(client, library)
    oa["fail"] = True
    out = await _build(client, library)
    assert out["failed"] >= 1
    assert _edges(await _graph(client, library)) == {("a", "b")}


async def test_build_is_batched_with_a_cursor(client, library, oa, pdf_refs):
    for i in range(5):
        await _item(client, library, f"p{i}", f"Paper {i}", DOI=f"10.1/{i}")
    r = await client.post(f"/libraries/{library}/citations/build", params={"limit": 2})
    first = r.json()
    assert first["checked"] == 2 and first["next_after"] is not None
    last = await _build(client, library, limit=2)
    assert last["next_after"] is None


# ── the graph ────────────────────────────────────────────────────────────────────────


async def test_graph_lists_every_paper_with_counts(client, library, oa, pdf_refs):
    await _item(client, library, "a", "Paper A", 2018, DOI="10.1/a",
                author=[{"family": "Ng", "given": "A"}])
    await _item(client, library, "b", "Paper B", 2019, DOI="10.1/b")
    await _item(client, library, "lonely", "Unconnected", 2020)
    oa["works"] = {"10.1/a": _work("W1", "10.1/a", ["W2"]), "10.1/b": _work("W2", "10.1/b")}
    await _build(client, library)
    graph = await _graph(client, library)
    by_key = {n["citation_key"]: n for n in graph["nodes"]}
    assert set(by_key) == {"a", "b", "lonely"}
    assert by_key["a"]["cites"] == 1 and by_key["a"]["cited_by"] == 0
    assert by_key["b"]["cited_by"] == 1
    assert by_key["a"]["year"] == 2018 and by_key["a"]["authors"] == "Ng, A"
    assert graph["checked"] == 3 and graph["total"] == 3


async def test_graph_before_any_build_has_no_edges(client, library):
    await _item(client, library, "a", "Paper A")
    graph = await _graph(client, library)
    assert graph["edges"] == [] and graph["checked"] == 0 and graph["total"] == 1


async def test_trashed_papers_leave_the_graph(client, library, oa, pdf_refs):
    await _item(client, library, "a", "Paper A", DOI="10.1/a")
    b = await _item(client, library, "b", "Paper B", DOI="10.1/b")
    oa["works"] = {"10.1/a": _work("W1", "10.1/a", ["W2"]), "10.1/b": _work("W2", "10.1/b")}
    await _build(client, library)
    await client.delete(f"/items/{b}")
    graph = await _graph(client, library)
    assert [n["citation_key"] for n in graph["nodes"]] == ["a"]
    assert graph["edges"] == []


async def test_permissions(client, library, make_user, login, oa, pdf_refs):
    group = (await client.post("/groups", json={"slug": "lab", "name": "Lab"})).json()
    viewer = await make_user("viewer@e4e.local", "V")
    await client.post(f"/groups/{group['id']}/members", json={"email": viewer.email})
    await client.post(
        f"/libraries/{library}/shares", json={"group_id": group["id"], "access_level": "view"}
    )
    login(viewer)
    assert (await client.get(f"/libraries/{library}/citations")).status_code == 200
    assert (await client.post(f"/libraries/{library}/citations/build")).status_code == 403
    login(await make_user("eve@e4e.local", "E"))
    assert (await client.get(f"/libraries/{library}/citations")).status_code in (403, 404)


# ── the OpenAlex client ──────────────────────────────────────────────────────────────


async def test_openalex_client_batches_and_normalizes(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url)
        dois = request.url.params["filter"][4:].split("|")
        body = {"results": [_work(f"W{i}", d) for i, d in enumerate(dois)]}
        return httpx.Response(200, content=json.dumps(body))

    monkeypatch.setattr(openalex, "_transport", lambda: httpx.MockTransport(handler))
    dois = [f"10.1/{i}" for i in range(120)]
    out = await openalex.works_by_doi(dois)
    assert len(seen) == 3  # 50 per request
    assert out["10.1/0"] == ("W0", [])
    assert all(u.params.get("mailto") for u in seen)


async def test_a_missing_pdf_in_storage_is_a_failure_not_a_crash(
    client, library, oa, pdf_refs, db, monkeypatch
):
    from botocore.exceptions import ClientError

    a = await _item(client, library, "a", "Paper A")
    await _attach_pdf(db, a, b"%PDF-a")

    def gone(key):
        raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    monkeypatch.setattr("app.routers.citations.download_bytes", gone)
    out = await _build(client, library)
    assert out["failed"] == 1
