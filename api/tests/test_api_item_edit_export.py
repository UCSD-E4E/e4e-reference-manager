"""Edits must reach the exported .bib. Imported items keep their original BibTeX for
verbatim export — but only while it still describes the item: once an edit, restore,
PDF fill or duplicate merge changes it, export regenerates from the current data."""


BIB = b"@article{a1,title={Old title},author={Smith, Jane},year={2020},keywords={keep-me}}\n"


async def _import(client, library):
    await client.post(f"/libraries/{library}/import", files={"file": ("a.bib", BIB)})
    return (await client.get(f"/libraries/{library}/items")).json()["items"][0]


async def _export(client, library):
    return (await client.get(f"/libraries/{library}/export.bib")).text


async def test_untouched_import_exports_verbatim(client, library):
    await _import(client, library)
    assert "keep-me" in await _export(client, library)  # a field CSL doesn't carry


async def test_edit_reaches_the_export(client, library):
    item = await _import(client, library)
    csl = {**item["csl_json"], "title": "New title"}
    r = await client.patch(
        f"/items/{item['id']}",
        json={"version": item["version"], "csl_json": csl, "citation_key": "b1"},
    )
    assert r.status_code == 200

    out = await _export(client, library)
    assert "New title" in out and "Old title" not in out
    assert "@article{b1" in out.replace(" ", "")


async def test_noop_save_keeps_the_original_bibtex(client, library):
    item = await _import(client, library)
    r = await client.patch(
        f"/items/{item['id']}",
        json={"version": item["version"], "csl_json": item["csl_json"], "citation_key": "a1"},
    )
    assert r.status_code == 200
    assert "keep-me" in await _export(client, library)


async def test_restore_reaches_the_export(client, library):
    item = await _import(client, library)
    edited = (
        await client.patch(
            f"/items/{item['id']}",
            json={"version": item["version"], "csl_json": {**item["csl_json"], "title": "Second"}},
        )
    ).json()
    await client.patch(
        f"/items/{item['id']}",
        json={"version": edited["version"], "csl_json": {**item["csl_json"], "title": "Third"}},
    )
    history = (await client.get(f"/items/{item['id']}/history")).json()
    second = next(e for e in history if "Second" in e["summary"])
    await client.post(f"/items/{item['id']}/restore", params={"event_id": second["id"]})
    out = await _export(client, library)
    assert "Second" in out and "Third" not in out


async def test_stale_version_is_a_conflict(client, library):
    item = await _import(client, library)
    ok = await client.patch(
        f"/items/{item['id']}", json={"version": item["version"], "csl_json": item["csl_json"]}
    )
    assert ok.status_code == 200
    stale = await client.patch(
        f"/items/{item['id']}", json={"version": item["version"], "csl_json": item["csl_json"]}
    )
    assert stale.status_code == 409
