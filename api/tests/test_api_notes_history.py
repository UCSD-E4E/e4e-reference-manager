"""Notes, audit history, restore, and the library activity feed."""


async def _make_item(client, lib, title="Original"):
    return (
        await client.post(
            f"/libraries/{lib}/items", json={"csl_json": {"type": "article-journal", "title": title}}
        )
    ).json()


async def test_notes_add_list_delete(client, library):
    item = await _make_item(client, library)
    add = await client.post(f"/items/{item['id']}/notes", json={"body": "first note"})
    assert add.status_code == 201

    notes = (await client.get(f"/items/{item['id']}/notes")).json()
    assert [n["body"] for n in notes] == ["first note"]

    assert (await client.delete(f"/notes/{notes[0]['id']}")).status_code == 204
    assert (await client.get(f"/items/{item['id']}/notes")).json() == []


async def test_history_records_create_and_update(client, library):
    item = await _make_item(client, library, "Original")
    await client.patch(
        f"/items/{item['id']}",
        json={"version": item["version"], "csl_json": {"type": "article-journal", "title": "Edited"}},
    )
    hist = (await client.get(f"/items/{item['id']}/history")).json()
    ops = [e["operation"] for e in hist]
    assert ops == ["update", "create"]  # newest first


async def test_restore_reverts_and_bumps_version(client, library):
    item = await _make_item(client, library, "Original")
    await client.patch(
        f"/items/{item['id']}",
        json={"version": item["version"], "csl_json": {"type": "article-journal", "title": "Edited"}},
    )
    hist = (await client.get(f"/items/{item['id']}/history")).json()
    create_ev = next(e for e in hist if e["operation"] == "create")

    restored = await client.post(f"/items/{item['id']}/restore", params={"event_id": create_ev["id"]})
    assert restored.status_code == 200
    body = restored.json()
    assert body["title"] == "Original"
    assert body["version"] == 3  # create(1) -> update(2) -> restore(3)


async def test_activity_feed_lists_events(client, library):
    item = await _make_item(client, library)
    await client.post(f"/items/{item['id']}/notes", json={"body": "n"})
    feed = (await client.get(f"/libraries/{library}/activity")).json()
    kinds = {(e["entity_type"], e["operation"]) for e in feed}
    assert ("item", "create") in kinds
    assert ("note", "create") in kinds
