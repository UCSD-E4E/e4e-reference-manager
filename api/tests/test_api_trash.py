"""Library trash: deleting a reference moves it to its library's trash instead of
destroying it. Trashed references vanish from every view (lists, search, collections,
exports, item endpoints) until restored; the trash is purged after 30 days."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuditEvent, Item
from app.trash import purge_expired_trash


async def _item(client, library, title="Coral Acoustics", **csl):
    r = await client.post(
        f"/libraries/{library}/items",
        json={"csl_json": {"type": "article-journal", "title": title, **csl}},
    )
    assert r.status_code == 201
    return r.json()


async def _trash(client, item_id):
    r = await client.delete(f"/items/{item_id}")
    assert r.status_code == 204


async def test_delete_moves_to_trash(client, library):
    it = await _item(client, library)
    await _trash(client, it["id"])

    assert (await client.get(f"/items/{it['id']}")).status_code == 404
    assert (await client.get(f"/libraries/{library}/items")).json()["total"] == 0

    [trashed] = (await client.get(f"/libraries/{library}/trash")).json()
    assert trashed["item"]["id"] == it["id"]
    assert trashed["deleted_by"]["email"] == "alice@e4e.local"
    deleted = datetime.fromisoformat(trashed["deleted_at"])
    purge = datetime.fromisoformat(trashed["purge_at"])
    assert abs((purge - deleted) - timedelta(days=30)) < timedelta(seconds=1)


async def test_trashed_items_are_hidden_everywhere(client, library):
    keep = await _item(client, library, "Keep Reef Paper")
    gone = await _item(client, library, "Gone Reef Paper")
    coll = (
        await client.post(f"/libraries/{library}/collections", json={"name": "C"})
    ).json()
    for it in (keep, gone):
        await client.post(f"/collections/{coll['id']}/items/{it['id']}")
    await _trash(client, gone["id"])

    search = await client.get(
        f"/libraries/{library}/search", params={"q": "reef", "mode": "keyword"}
    )
    assert [i["id"] for i in search.json()["items"]] == [keep["id"]]
    listed = await client.get(f"/libraries/{library}/items", params={"q": "reef"})
    assert [i["id"] for i in listed.json()["items"]] == [keep["id"]]
    in_coll = (await client.get(f"/collections/{coll['id']}/items")).json()
    assert [i["id"] for i in in_coll] == [keep["id"]]
    bib = (await client.get(f"/libraries/{library}/export.bib")).text
    assert "Keep Reef Paper" in bib and "Gone Reef Paper" not in bib
    patch = await client.patch(f"/items/{gone['id']}", json={"version": 1, "csl_json": {}})
    assert patch.status_code == 404


async def test_restore_brings_it_back_with_its_notes_and_collections(client, library):
    it = await _item(client, library)
    coll = (
        await client.post(f"/libraries/{library}/collections", json={"name": "C"})
    ).json()
    await client.post(f"/collections/{coll['id']}/items/{it['id']}")
    await client.post(f"/items/{it['id']}/notes", json={"body": "important"})
    await _trash(client, it["id"])

    r = await client.post(f"/libraries/{library}/trash/{it['id']}/restore")
    assert r.status_code == 200
    assert r.json()["id"] == it["id"]
    assert (await client.get(f"/items/{it['id']}")).status_code == 200
    notes = (await client.get(f"/items/{it['id']}/notes")).json()
    assert [n["body"] for n in notes] == ["important"]
    in_coll = (await client.get(f"/collections/{coll['id']}/items")).json()
    assert [i["id"] for i in in_coll] == [it["id"]]
    assert (await client.get(f"/libraries/{library}/trash")).json() == []


async def test_delete_forever_from_trash(client, library, db):
    it = await _item(client, library)
    await _trash(client, it["id"])
    r = await client.delete(f"/libraries/{library}/trash/{it['id']}")
    assert r.status_code == 204
    assert (await client.get(f"/libraries/{library}/trash")).json() == []
    row = await db.scalar(
        select(Item).where(Item.id == it["id"]).execution_options(include_trashed=True)
    )
    assert row is None


async def test_only_trashed_items_can_be_restored_or_purged(client, library):
    it = await _item(client, library)  # not in the trash
    assert (await client.post(f"/libraries/{library}/trash/{it['id']}/restore")).status_code == 404
    assert (await client.delete(f"/libraries/{library}/trash/{it['id']}")).status_code == 404
    assert (await client.get(f"/items/{it['id']}")).status_code == 200


async def test_empty_trash(client, library):
    a, b = await _item(client, library, "A"), await _item(client, library, "B")
    await _trash(client, a["id"])
    await _trash(client, b["id"])
    r = await client.delete(f"/libraries/{library}/trash")
    assert r.status_code == 200
    assert r.json() == {"purged": 2}
    assert (await client.get(f"/libraries/{library}/trash")).json() == []


async def test_trash_is_per_library(client, library):
    other = (await client.post("/libraries", json={"name": "Other"})).json()["id"]
    it = await _item(client, library)
    await _trash(client, it["id"])
    assert (await client.get(f"/libraries/{other}/trash")).json() == []
    assert (await client.post(f"/libraries/{other}/trash/{it['id']}/restore")).status_code == 404


async def test_trash_permissions(client, library, make_user, login, alice):
    """Viewers can see the trash; restoring and purging need edit access."""
    it = await _item(client, library)
    await _trash(client, it["id"])
    group = (await client.post("/groups", json={"slug": "lab", "name": "Lab"})).json()
    bob = await make_user("bob@e4e.local", "Bob")
    await client.post(f"/groups/{group['id']}/members", json={"email": bob.email})
    await client.post(
        f"/libraries/{library}/shares", json={"group_id": group["id"], "access_level": "view"}
    )

    login(bob)
    assert len((await client.get(f"/libraries/{library}/trash")).json()) == 1
    assert (await client.post(f"/libraries/{library}/trash/{it['id']}/restore")).status_code == 403
    assert (await client.delete(f"/libraries/{library}/trash/{it['id']}")).status_code == 403
    assert (await client.delete(f"/libraries/{library}/trash")).status_code == 403

    login(await make_user("eve@e4e.local", "Eve"))
    assert (await client.get(f"/libraries/{library}/trash")).status_code in (403, 404)


async def test_trash_and_restore_are_in_history(client, library, db):
    it = await _item(client, library, "History Paper")
    await _trash(client, it["id"])
    await client.post(f"/libraries/{library}/trash/{it['id']}/restore")
    rows = await db.execute(
        select(AuditEvent.operation, AuditEvent.summary)
        .where(AuditEvent.entity_id == it["id"])
        .order_by(AuditEvent.occurred_at)
    )
    ops = [(op, summary) for op, summary in rows]
    assert ("delete", "Moved “History Paper” to the trash") in ops
    assert ("restore", "Restored “History Paper” from the trash") in ops


async def test_purge_expired_trash(client, library, db):
    old = await _item(client, library, "Old")
    recent = await _item(client, library, "Recent")
    live = await _item(client, library, "Live")
    for it in (old, recent):
        await _trash(client, it["id"])
    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(Item).where(Item.id.in_([old["id"], recent["id"]]))
        .execution_options(include_trashed=True)
    )
    by_title = {i.title: i for i in rows.scalars()}
    by_title["Old"].deleted_at = now - timedelta(days=31)
    by_title["Recent"].deleted_at = now - timedelta(days=29)
    await db.commit()

    assert await purge_expired_trash(db) == 1
    trash = (await client.get(f"/libraries/{library}/trash")).json()
    assert [t["item"]["title"] for t in trash] == ["Recent"]
    assert (await client.get(f"/items/{live['id']}")).status_code == 200
    purged = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == old["id"], AuditEvent.operation == "purge"
        )
    )
    assert purged is not None


@pytest.mark.parametrize("path", ["/url", "/text"])
async def test_trashed_items_attachments_are_hidden(client, library, monkeypatch, path):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.routers.attachments.presigned_get_url", lambda key, name=None: "https://s3/x"
    )
    it = await _item(client, library)
    att = (
        await client.post(
            f"/items/{it['id']}/attachments",
            files={"file": ("p.pdf", b"%PDF-1.4 x", "application/pdf")},
        )
    ).json()
    await _trash(client, it["id"])
    assert (await client.get(f"/attachments/{att['id']}{path}")).status_code == 404


async def test_purge_loop_keeps_going_after_a_failure(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    import app.trash as trash

    runs = []

    async def flaky_purge(session):
        runs.append(session)
        if len(runs) == 1:
            raise RuntimeError("db hiccup")
        return 0

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError  # app shutdown

    @asynccontextmanager
    async def session_factory():
        yield "session"

    monkeypatch.setattr(trash, "purge_expired_trash", flaky_purge)
    monkeypatch.setattr(trash.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await trash.purge_trash_forever(session_factory)
    assert len(runs) == 2  # the failed run didn't stop the loop
    assert sleeps == [trash.PURGE_INTERVAL_SECONDS] * 2
