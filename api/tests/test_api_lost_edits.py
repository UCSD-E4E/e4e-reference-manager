"""Optimistic locking must hold under concurrency: two edits made from the same version
can't both win (the second gets a 409 instead of silently overwriting the first)."""
import asyncio

from app import embeddings

CSL = {"type": "article-journal", "title": "Original"}


async def _item(client, library):
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": CSL})
    assert r.status_code == 201
    return r.json()


def _hold_embeddings_until_both_arrive(monkeypatch):
    """Both requests pause in the (awaited) embedding call, so both have read the item and
    passed any unlocked version check before either writes: the race, made deterministic."""
    arrived, both = [], asyncio.Event()

    async def embed(csl):
        arrived.append(csl)
        if len(arrived) == 2:
            both.set()
        try:
            await asyncio.wait_for(both.wait(), 2)
        except TimeoutError:
            pass
        return None

    monkeypatch.setattr(embeddings, "embed_item_csl", embed)


async def test_concurrent_edits_from_the_same_version_conflict(client, library, monkeypatch):
    item = await _item(client, library)
    _hold_embeddings_until_both_arrive(monkeypatch)

    async def edit(title):
        body = {"version": item["version"], "csl_json": {**CSL, "title": title}}
        return await client.patch(f"/items/{item['id']}", json=body)

    a, b = await asyncio.gather(edit("Edit A"), edit("Edit B"))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    winner = a if a.status_code == 200 else b

    final = (await client.get(f"/items/{item['id']}")).json()
    assert final["version"] == item["version"] + 1
    assert final["title"] == winner.json()["title"]
    hist = (await client.get(f"/items/{item['id']}/history")).json()
    assert [e["operation"] for e in hist] == ["update", "create"]


async def test_stale_version_still_conflicts(client, library):
    item = await _item(client, library)
    ok = await client.patch(
        f"/items/{item['id']}", json={"version": 1, "csl_json": {**CSL, "title": "New"}}
    )
    assert ok.status_code == 200
    stale = await client.patch(
        f"/items/{item['id']}", json={"version": 1, "csl_json": {**CSL, "title": "Old"}}
    )
    assert stale.status_code == 409
    assert (await client.get(f"/items/{item['id']}")).json()["title"] == "New"


async def test_restore_after_a_concurrent_edit_bumps_past_it(client, library):
    item = await _item(client, library)
    hist = (await client.get(f"/items/{item['id']}/history")).json()
    await client.patch(
        f"/items/{item['id']}", json={"version": 1, "csl_json": {**CSL, "title": "Edited"}}
    )
    r = await client.post(f"/items/{item['id']}/restore", params={"event_id": hist[0]["id"]})
    assert r.status_code == 200
    assert r.json()["version"] == 3
    assert r.json()["title"] == "Original"
