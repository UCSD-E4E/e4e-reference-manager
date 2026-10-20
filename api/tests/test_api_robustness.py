"""Robustness: bad or oversized input is a 422 (never a 500 or a stored broken row),
export filenames survive any characters, auto-group generation ignores the trash and
scales, and embedding / ML-tag bookkeeping isn't lost or repeated.

Ollama is never hit: embed_text and chat are monkeypatched (see test_api_semantic)."""
import re
from urllib.parse import quote

import pytest
from sqlalchemy import event, select

import app.embeddings as emb
import app.llm as llm
from app.models import EMBEDDING_DIM, AutoGroup, Item, Tag, item_tag


async def _item(client, library, title="Paper", **csl):
    csl.setdefault("type", "article-journal")
    r = await client.post(
        f"/libraries/{library}/items", json={"csl_json": {"title": title, **csl}}
    )
    assert r.status_code == 201
    return r.json()


# ---------- export filenames ----------


@pytest.mark.parametrize("name", ["Ch 1 — intro", "Nguyễn", "参考文献", 'say "hi"; x'])
async def test_collection_export_filename_survives_any_name(client, library, name):
    coll = (
        await client.post(f"/libraries/{library}/collections", json={"name": name})
    ).json()
    r = await client.get(f"/collections/{coll['id']}/export.bib")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.isascii()
    assert f"filename*=UTF-8''{quote(name + '.bib', safe='')}" in cd
    # The plain fallback is a single well-formed quoted string.
    m = re.fullmatch(r'attachment; filename="([^"\\]+)"; filename\*=\S+', cd)
    assert m and m.group(1).endswith(".bib")


async def test_ascii_export_filename_kept(client, library):
    r = await client.get(f"/libraries/{library}/export.bib")
    assert 'filename="Test Lib.bib"' in r.headers["content-disposition"]


# ---------- auto-group params validation ----------


@pytest.mark.parametrize(
    "kind,params",
    [
        ("tag", {"name": 5}),
        ("tag", {}),
        ("tag", {"name": "x", "source": 3}),
        ("search", {"q": 5}),
        ("search", {}),
        ("field", {"field": "journal", "value": 5}),
        ("field", {"field": "colour", "value": "red"}),
        ("field", {"field": "year"}),
        ("field", {"field": "year", "value": "twenty"}),
    ],
)
async def test_bad_auto_group_params_are_422_and_not_stored(client, library, kind, params):
    r = await client.post(
        f"/libraries/{library}/auto-groups", json={"name": "G", "kind": kind, "params": params}
    )
    assert r.status_code == 422
    listed = await client.get(f"/libraries/{library}/auto-groups")
    assert listed.status_code == 200
    assert listed.json() == []


async def test_unknown_auto_group_kind_rejected(client, library):
    r = await client.post(
        f"/libraries/{library}/auto-groups", json={"name": "G", "kind": "nope", "params": {}}
    )
    assert r.status_code in (400, 422)


# ---------- length limits ----------


async def test_overlong_names_are_422(client, library, monkeypatch):
    long = "x" * 256
    r = await client.post(f"/libraries/{library}/collections", json={"name": long})
    assert r.status_code == 422
    r = await client.post(
        f"/libraries/{library}/auto-groups",
        json={"name": long, "kind": "search", "params": {"q": "x"}},
    )
    assert r.status_code == 422

    async def boom(prompt, **kw):
        raise AssertionError("not called")

    monkeypatch.setattr(llm, "chat", boom)
    it = await _item(client, library)
    r = await client.post(
        f"/items/{it['id']}/suggest-tags", params={"apply": True}, json={"tags": [long]}
    )
    assert r.status_code == 422


async def test_overlong_llm_tags_are_truncated(client, library, monkeypatch):
    async def chat(prompt, **kw):
        return '["' + "y" * 300 + '"]'

    monkeypatch.setattr(llm, "chat", chat)
    it = await _item(client, library)
    r = await client.post(f"/items/{it['id']}/suggest-tags", params={"apply": True})
    assert r.status_code == 200
    assert [len(t["name"]) for t in r.json()["applied"]] == [255]


async def test_generated_group_names_are_truncated(client, library):
    await _item(client, library, **{"container-title": "J" * 300})
    await _item(client, library, author=[{"family": "F" * 300}])
    for source in ("journal", "author"):
        r = await client.post(
            f"/libraries/{library}/auto-groups/generate", json={"source": source}
        )
        assert r.status_code == 200
        assert r.json()["created"] == 1
    groups = (await client.get(f"/libraries/{library}/auto-groups")).json()
    assert sorted(len(g["name"]) for g in groups) == [255, 255]
    assert all(g["count"] == 1 for g in groups)  # params keep the full value


async def _attachment(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    it = await _item(client, library)
    r = await client.post(
        f"/items/{it['id']}/attachments",
        files={"file": ("p.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    return r.json()["id"]


@pytest.mark.parametrize(
    "body", [{"color": "#" + "f" * 16}, {"page": 0}, {"page": 2**31}, {"page": -3}]
)
async def test_bad_annotation_is_422(client, library, monkeypatch, body):
    att = await _attachment(client, library, monkeypatch)
    r = await client.post(f"/attachments/{att}/annotations", json=body)
    assert r.status_code == 422


async def test_overlong_annotation_color_update_is_422(client, library, monkeypatch):
    att = await _attachment(client, library, monkeypatch)
    ann = (await client.post(f"/attachments/{att}/annotations", json={})).json()
    r = await client.patch(f"/annotations/{ann['id']}", json={"color": "c" * 17})
    assert r.status_code == 422


# ---------- generation ignores the trash ----------


async def test_generate_ignores_trashed_items(client, library, monkeypatch):
    gone = await _item(
        client, library, author=[{"family": "Ghost"}], **{"container-title": "Gone J"}
    )
    await _item(client, library, author=[{"family": "Here"}], **{"container-title": "Here J"})
    assert (await client.delete(f"/items/{gone['id']}")).status_code == 204
    for source in ("journal", "author"):
        await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": source})
    groups = (await client.get(f"/libraries/{library}/auto-groups")).json()
    names = sorted(g["name"] for g in groups)
    assert names == ["Here", "Here J"]


async def test_ml_tag_groups_count_only_live_items(client, library, monkeypatch):
    async def chat(prompt, **kw):
        return '["reefs"]'

    monkeypatch.setattr(llm, "chat", chat)
    items = [await _item(client, library, title=f"Reef {i}") for i in range(3)]
    for it in items:
        await client.post(f"/items/{it['id']}/suggest-tags", params={"apply": True})
    await client.delete(f"/items/{items[0]['id']}")
    r = await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"})
    assert r.json()["created"] == 0  # only 2 live papers share the tag


# ---------- auto-group query counts ----------


@pytest.fixture
def count_queries(engine):
    stmts: list[str] = []

    def _on(conn, cursor, statement, *a):
        stmts.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _on)
    yield stmts
    event.remove(engine.sync_engine, "before_cursor_execute", _on)


async def test_listing_and_generating_take_constant_queries(
    client, library, db, count_queries
):
    for y in range(2000, 2030):
        await _item(client, library, title=f"P{y}", issued={"date-parts": [[y]]})
    await _item(client, library, title="Tagged", author=[{"family": "Smith"}])
    r = await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "year"})
    assert r.json()["created"] == 30

    count_queries.clear()
    r = await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "year"})
    assert r.json()["created"] == 0
    assert len(count_queries) < 10

    await client.post(
        f"/libraries/{library}/auto-groups",
        json={"name": "S", "kind": "search", "params": {"q": "tagged"}},
    )
    await client.post(
        f"/libraries/{library}/auto-groups",
        json={"name": "A", "kind": "field", "params": {"field": "author", "value": "Smith"}},
    )
    count_queries.clear()
    groups = (await client.get(f"/libraries/{library}/auto-groups")).json()
    assert len(groups) == 32
    assert len(count_queries) < 10
    counts = {g["name"]: g["count"] for g in groups}
    assert counts["2005"] == 1 and counts["S"] == 1 and counts["A"] == 1


# ---------- embeddings are kept ----------


def _vec(axis: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[axis] = 1.0
    return v


async def test_topic_failure_keeps_backfilled_embeddings(client, library, db, monkeypatch):
    async def none(text):
        return None

    monkeypatch.setattr(emb, "embed_text", none)
    for i in range(6):
        await _item(client, library, title=f"Reef {i}")

    async def embed(text):
        return _vec(0)

    async def down(prompt, **kw):
        return None

    monkeypatch.setattr(emb, "embed_text", embed)
    monkeypatch.setattr(llm, "chat", down)
    r = await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "topics"})
    assert r.status_code == 503
    rows = (await db.execute(select(Item.embedding))).scalars().all()
    assert len(rows) == 6 and all(v is not None for v in rows)


async def test_reindex_only_embeds_missing_unless_forced(client, library, monkeypatch):
    calls = []

    async def embed(text):
        calls.append(text)
        return _vec(1)

    monkeypatch.setattr(emb, "embed_text", embed)
    await _item(client, library, title="Has one")

    async def none(text):
        return None

    monkeypatch.setattr(emb, "embed_text", none)
    await _item(client, library, title="Lacks one")
    monkeypatch.setattr(emb, "embed_text", embed)

    calls.clear()
    r = await client.post(f"/libraries/{library}/reindex")
    assert r.json() == {"items": 1, "embedded": 1}
    assert len(calls) == 1

    calls.clear()
    r = await client.post(f"/libraries/{library}/reindex", params={"force": True})
    assert r.json() == {"items": 2, "embedded": 2}
    assert len(calls) == 2


# ---------- ML tag bookkeeping ----------


async def test_item_with_same_named_other_tag_is_not_asked_again(
    client, library, db, monkeypatch
):
    it = await _item(client, library, title="Reef survey")
    topic = Tag(library_id=it["library_id"], name="reefs", source="topic")
    db.add(topic)
    await db.flush()
    await db.execute(item_tag.insert().values(item_id=it["id"], tag_id=topic.id))
    await db.commit()

    calls = []

    async def chat(prompt, **kw):
        calls.append(prompt)
        return '["reefs"]'

    monkeypatch.setattr(llm, "chat", chat)
    gen = f"/libraries/{library}/auto-groups/generate"
    r = await client.post(gen, json={"source": "ml_tags"})
    assert r.json()["tagged"] == 1 and r.json()["remaining"] == 0
    calls.clear()
    r = await client.post(gen, json={"source": "ml_tags"})
    assert calls == []
    assert r.json()["remaining"] == 0
    sources = {
        t.source
        for t in (await db.execute(select(Tag).where(Tag.name == "reefs"))).scalars()
    }
    assert sources == {"topic", "ml"}
    assert (await db.scalar(select(AutoGroup.id).limit(1))) is None
