"""Merging duplicates already in a project (e.g. from imports before de-duplication).
Same rule as import: DOI, else normalized title + year. The oldest copy is kept and
the rest are merged into it — PDFs, notes, tags and collections move over, and
metadata only the duplicate had fills the kept item's blanks."""


async def _add(client, library, **csl):
    csl.setdefault("type", "article")
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    return r.json()


async def _items(client, library):
    return (await client.get(f"/libraries/{library}/items")).json()["items"]


async def test_dry_run_reports_without_changing_anything(client, library):
    keep = await _add(client, library, title="Reefs", DOI="10.1/x", issued={"date-parts": [[2020]]})
    dup = await _add(client, library, title="Reefs!", DOI="https://doi.org/10.1/X")

    r = await client.post(f"/libraries/{library}/dedupe", params={"dry_run": True})
    assert r.status_code == 200
    assert r.json() == {
        "merged": 1,
        "groups": [{"kept": keep["id"], "merged": [dup["id"]], "matched_on": "doi"}],
    }
    assert len(await _items(client, library)) == 2


async def test_merge_moves_everything_onto_the_oldest_copy(client, library):
    keep = await _add(client, library, title="Deep {L}earning", issued={"date-parts": [[2020]]})
    dup = await _add(
        client,
        library,
        title="deep learning.",
        issued={"date-parts": [[2020]]},
        abstract="Only the duplicate had this.",
    )
    await client.post(f"/items/{dup['id']}/notes", json={"body": "note on dup"})
    await client.post(
        f"/items/{dup['id']}/suggest-tags", params={"apply": True}, json={"tags": ["reefs"]}
    )
    col = (await client.post(f"/libraries/{library}/collections", json={"name": "Reading"})).json()
    await client.post(f"/collections/{col['id']}/items/{dup['id']}")

    r = await client.post(f"/libraries/{library}/dedupe")
    assert r.json()["merged"] == 1
    assert r.json()["groups"][0]["matched_on"] == "title"

    items = await _items(client, library)
    assert [i["id"] for i in items] == [keep["id"]]
    kept = (await client.get(f"/items/{keep['id']}")).json()
    assert kept["csl_json"]["abstract"] == "Only the duplicate had this."
    assert kept["csl_json"]["title"] == "Deep {L}earning"  # the kept item's own fields win
    assert kept["version"] == keep["version"] + 1
    notes = (await client.get(f"/items/{keep['id']}/notes")).json()
    assert [n["body"] for n in notes] == ["note on dup"]
    assert [t["name"] for t in (await client.get(f"/items/{keep['id']}/tags")).json()] == ["reefs"]
    col_items = (await client.get(f"/collections/{col['id']}/items")).json()
    assert [i["id"] for i in col_items] == [keep["id"]]


async def test_three_copies_collapse_and_non_duplicates_survive(client, library):
    a = await _add(client, library, title="Same", DOI="10.1/s")
    await _add(client, library, title="Same", DOI="10.1/s")
    await _add(client, library, title="Same", DOI="10.1/S")
    other_year = await _add(client, library, title="Other", issued={"date-parts": [[2020]]})
    await _add(client, library, title="Other", issued={"date-parts": [[2021]]})

    r = (await client.post(f"/libraries/{library}/dedupe")).json()
    assert r["merged"] == 2
    ids = {i["id"] for i in await _items(client, library)}
    assert a["id"] in ids and other_year["id"] in ids
    assert len(ids) == 3


async def test_nothing_to_merge(client, library):
    await _add(client, library, title="One")
    assert (await client.post(f"/libraries/{library}/dedupe")).json() == {"merged": 0, "groups": []}
