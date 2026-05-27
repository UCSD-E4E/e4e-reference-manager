"""Phase 4: Collections (folders within a library) + per-collection .bib export.

A collection groups items within one library; items can be added/removed; the collection
exports to .bib (only its members). RBAC follows the library: view to read/export, edit
to create/modify."""


async def _item(client, lib, key, title):
    return (
        await client.post(
            f"/libraries/{lib}/items",
            json={"citation_key": key, "csl_json": {"type": "article-journal", "title": title}},
        )
    ).json()


async def test_create_and_list_collections(client, library):
    c = await client.post(f"/libraries/{library}/collections", json={"name": "Chapter 1"})
    assert c.status_code == 201
    assert c.json()["name"] == "Chapter 1"
    assert c.json()["library_id"] == library

    lst = await client.get(f"/libraries/{library}/collections")
    assert lst.status_code == 200
    assert [x["name"] for x in lst.json()] == ["Chapter 1"]


async def test_add_remove_and_list_collection_items(client, library):
    coll = (await client.post(f"/libraries/{library}/collections", json={"name": "C"})).json()
    it = await _item(client, library, "k1", "Title One")

    add = await client.post(f"/collections/{coll['id']}/items/{it['id']}")
    assert add.status_code == 204
    # idempotent: adding again is still fine, no duplicate
    await client.post(f"/collections/{coll['id']}/items/{it['id']}")

    items = await client.get(f"/collections/{coll['id']}/items")
    assert [i["id"] for i in items.json()] == [it["id"]]

    rm = await client.delete(f"/collections/{coll['id']}/items/{it['id']}")
    assert rm.status_code == 204
    assert (await client.get(f"/collections/{coll['id']}/items")).json() == []


async def test_collection_export_only_includes_member_items(client, library):
    coll = (await client.post(f"/libraries/{library}/collections", json={"name": "C"})).json()
    inside = await _item(client, library, "inkey", "Inside Paper")
    await _item(client, library, "outkey", "Outside Paper")  # not added
    await client.post(f"/collections/{coll['id']}/items/{inside['id']}")

    exp = await client.get(f"/collections/{coll['id']}/export.bib")
    assert exp.status_code == 200
    assert "inkey" in exp.text
    assert "outkey" not in exp.text


async def test_cross_library_item_rejected(client, library):
    other = (await client.post("/libraries", json={"name": "Other"})).json()["id"]
    coll = (await client.post(f"/libraries/{library}/collections", json={"name": "C"})).json()
    foreign = await _item(client, other, "x", "X")
    r = await client.post(f"/collections/{coll['id']}/items/{foreign['id']}")
    assert r.status_code == 400


async def test_nested_collection_parent_must_be_same_library(client, library):
    other = (await client.post("/libraries", json={"name": "Other"})).json()["id"]
    foreign = (await client.post(f"/libraries/{other}/collections", json={"name": "F"})).json()
    bad = await client.post(
        f"/libraries/{library}/collections",
        json={"name": "Child", "parent_id": foreign["id"]},
    )
    assert bad.status_code == 400


async def test_delete_collection(client, library):
    coll = (await client.post(f"/libraries/{library}/collections", json={"name": "C"})).json()
    assert (await client.delete(f"/collections/{coll['id']}")).status_code == 204
    assert (await client.get(f"/libraries/{library}/collections")).json() == []


async def test_collection_rbac(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")
    carol = await make_user("carol@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "t", "name": "T"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "A"})).json()["id"]
    coll = (await client.post(f"/libraries/{lib}/collections", json={"name": "C"})).json()
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "view"}
    )

    # No access -> 404
    login(carol)
    assert (await client.get(f"/libraries/{lib}/collections")).status_code == 404

    # View-only (bob): read + export ok, create denied
    login(bob)
    assert (await client.get(f"/libraries/{lib}/collections")).status_code == 200
    assert (await client.get(f"/collections/{coll['id']}/export.bib")).status_code == 200
    denied = await client.post(f"/libraries/{lib}/collections", json={"name": "Nope"})
    assert denied.status_code == 403
