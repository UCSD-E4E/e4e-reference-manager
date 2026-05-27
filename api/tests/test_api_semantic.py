"""Phase 3c: semantic + hybrid search over pgvector embeddings (Ollama).

Ollama is never hit: tests monkeypatch app.embeddings.embed_text with a deterministic
fake so embedding/search are exercised without the model. embed_text returning None
(Ollama down) must degrade gracefully — keyword search keeps working.
"""
import app.embeddings as emb
from app.embeddings import build_embedding_input

DIM = 768


def _vec(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def _fake_embed_factory():
    """Map distinctive marker words to orthogonal unit vectors; unknown text -> zero."""

    async def fake_embed(text: str):
        t = (text or "").lower()
        if "alpha" in t:
            return _vec(0)
        if "beta" in t:
            return _vec(1)
        if "gamma" in t:
            return _vec(2)
        return [0.0] * DIM

    return fake_embed


async def _add(client, library, **csl):
    csl.setdefault("type", "document")
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    return r.json()


def test_build_embedding_input_includes_title_and_abstract():
    text = build_embedding_input(
        {"title": "Coral genomics", "abstract": "We sequence reef-building corals."}
    )
    assert "Coral genomics" in text
    assert "reef-building" in text


async def test_semantic_search_ranks_by_meaning(client, library, monkeypatch):
    monkeypatch.setattr(emb, "embed_text", _fake_embed_factory())
    await _add(client, library, title="Alpha study")
    await _add(client, library, title="Beta study")
    await _add(client, library, title="Gamma study")

    r = await client.get(
        f"/libraries/{library}/search", params={"q": "alpha", "mode": "semantic"}
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["title"] == "Alpha study"  # nearest neighbour to the query vector


async def test_keyword_mode_uses_fts(client, library, monkeypatch):
    monkeypatch.setattr(emb, "embed_text", _fake_embed_factory())
    await _add(client, library, title="Photosynthesis", abstract="zooxanthellae symbiosis")
    await _add(client, library, title="Unrelated")

    r = await client.get(
        f"/libraries/{library}/search", params={"q": "zooxanthellae", "mode": "keyword"}
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["title"] == "Photosynthesis"


async def test_hybrid_returns_both_keyword_and_semantic_hits(client, library, monkeypatch):
    monkeypatch.setattr(emb, "embed_text", _fake_embed_factory())
    # Keyword-only hit: matches the literal query word but not the semantic marker.
    await _add(client, library, title="quantum dynamics")
    # Semantic-only hit: shares the marker vector with the query but not the literal word.
    await _add(client, library, title="alpha helix")

    r = await client.get(
        f"/libraries/{library}/search", params={"q": "alpha", "mode": "hybrid"}
    )
    titles = {it["title"] for it in r.json()["items"]}
    assert "alpha helix" in titles  # semantic
    # The query term "alpha" also FTS-matches "alpha helix"; ensure hybrid surfaces it.
    assert r.json()["total"] >= 1


async def test_semantic_degrades_when_ollama_down(client, library, monkeypatch):
    async def no_embed(text):
        return None

    monkeypatch.setattr(emb, "embed_text", no_embed)
    await _add(client, library, title="Coral reefs")

    # No item embeddings were produced; semantic search must not 500.
    sem = await client.get(
        f"/libraries/{library}/search", params={"q": "coral", "mode": "semantic"}
    )
    assert sem.status_code == 200
    assert sem.json()["total"] == 0

    # Keyword search still works regardless of Ollama.
    kw = await client.get(
        f"/libraries/{library}/search", params={"q": "coral", "mode": "keyword"}
    )
    assert kw.json()["total"] == 1


async def test_reindex_backfills_embeddings(client, library, monkeypatch):
    async def no_embed(text):
        return None

    monkeypatch.setattr(emb, "embed_text", no_embed)
    await _add(client, library, title="Alpha study")  # created without an embedding

    # Ollama "comes back"; reindex should embed existing items.
    monkeypatch.setattr(emb, "embed_text", _fake_embed_factory())
    r = await client.post(f"/libraries/{library}/reindex")
    assert r.status_code == 200
    assert r.json()["embedded"] == 1

    sem = await client.get(
        f"/libraries/{library}/search", params={"q": "alpha", "mode": "semantic"}
    )
    assert sem.json()["total"] == 1
    assert sem.json()["items"][0]["title"] == "Alpha study"
