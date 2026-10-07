"""Deleting a project (library): manage access only; takes its contents with it;
leaves an audit record that outlives the library."""
from sqlalchemy import func, select

from app.models import AuditEvent, Item


async def _add_item(client, lib, title="A paper"):
    r = await client.post(
        f"/libraries/{lib}/items", json={"csl_json": {"type": "article", "title": title}}
    )
    assert r.status_code == 201
    return r.json()


async def test_owner_deletes_project_and_its_contents(client, library, alice, db):
    item = await _add_item(client, library)

    r = await client.delete(f"/libraries/{library}")
    assert r.status_code == 204

    assert (await client.get(f"/libraries/{library}")).status_code == 404
    assert all(lib["id"] != library for lib in (await client.get("/libraries")).json())
    assert (await client.get(f"/items/{item['id']}")).status_code == 404
    assert await db.scalar(select(func.count()).select_from(Item)) == 0


async def test_delete_is_audited_beyond_the_library(client, library, alice, db):
    await client.delete(f"/libraries/{library}")

    ev = (
        await db.execute(select(AuditEvent).where(AuditEvent.entity_type == "library"))
    ).scalar_one()
    assert ev.operation == "delete"
    assert ev.library_id is None  # survives the library's ON DELETE CASCADE
    assert ev.actor_id == alice.id
    assert "Test Lib" in ev.summary


async def test_edit_share_cannot_delete(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "team", "name": "Team"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "Alice lib"})).json()["id"]
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "edit"}
    )

    login(bob)
    assert (await client.delete(f"/libraries/{lib}")).status_code == 403

    login(alice)
    assert (await client.get(f"/libraries/{lib}")).status_code == 200


async def test_stranger_gets_404(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(alice)
    lib = (await client.post("/libraries", json={"name": "Private"})).json()["id"]

    login(bob)
    assert (await client.delete(f"/libraries/{lib}")).status_code == 404
