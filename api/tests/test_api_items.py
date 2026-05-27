"""Items CRUD, optimistic locking, and .bib import/export."""

ARTICLE = {
    "type": "article-journal",
    "title": "Hello World",
    "DOI": "10.1/x",
    "issued": {"date-parts": [[2020]]},
}


async def test_create_item_denormalizes_fields(client, library):
    r = await client.post(
        f"/libraries/{library}/items",
        json={"citation_key": "k1", "type": "article-journal", "csl_json": ARTICLE},
    )
    assert r.status_code == 201
    item = r.json()
    assert (item["title"], item["year"], item["doi"]) == ("Hello World", 2020, "10.1/x")
    assert (await client.get(f"/items/{item['id']}")).status_code == 200


async def test_optimistic_locking(client, library):
    item = (
        await client.post(
            f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": "A"}}
        )
    ).json()

    stale = await client.patch(
        f"/items/{item['id']}",
        json={"version": 999, "csl_json": {"type": "document", "title": "B"}},
    )
    assert stale.status_code == 409

    ok = await client.patch(
        f"/items/{item['id']}",
        json={"version": item["version"], "csl_json": {"type": "document", "title": "B"}},
    )
    assert ok.status_code == 200
    assert ok.json()["title"] == "B"
    assert ok.json()["version"] == item["version"] + 1


async def test_bib_import_flags_collisions_and_exports(client, library):
    bib = b"@article{a1,title={T1},author={X, Y},year={2021},doi={10.1/a}}\n"
    first = await client.post(f"/libraries/{library}/import", files={"file": ("a.bib", bib)})
    assert first.status_code == 200
    assert first.json()["imported"] == 1
    assert first.json()["key_collisions"] == []

    again = await client.post(f"/libraries/{library}/import", files={"file": ("a.bib", bib)})
    assert again.json()["key_collisions"] == ["a1"]  # preserved verbatim, flagged

    exp = await client.get(f"/libraries/{library}/export.bib")
    assert exp.status_code == 200
    assert "a1" in exp.text


async def test_search_by_title(client, library):
    for title in ("Coral reefs", "Machine learning"):
        await client.post(
            f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": title}}
        )
    r = await client.get(f"/libraries/{library}/items", params={"q": "coral"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["title"] == "Coral reefs"


async def test_delete_item(client, library):
    item = (
        await client.post(
            f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": "X"}}
        )
    ).json()
    assert (await client.delete(f"/items/{item['id']}")).status_code == 204
    assert (await client.get(f"/items/{item['id']}")).status_code == 404
