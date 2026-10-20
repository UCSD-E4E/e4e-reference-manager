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


def _make_pdf(text: str) -> bytes:
    import fitz  # PyMuPDF

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


async def _attach_annotated_pdf(client, item_id, text):
    r = await client.post(
        f"/items/{item_id}/attachments",
        files={"file": ("p.pdf", _make_pdf(text), "application/pdf")},
    )
    assert r.status_code == 201
    att = r.json()["id"]
    r = await client.post(
        f"/attachments/{att}/annotations",
        json={"page": 1, "rects": [], "comment": "kept highlight"},
    )
    assert r.status_code == 201
    return att


async def _assert_pdf_moved(client, library, keep, att, term):
    kept = (await client.get(f"/items/{keep['id']}")).json()
    assert [a["id"] for a in kept["attachments"]] == [att]
    anns = (await client.get(f"/attachments/{att}/annotations")).json()
    assert [a["comment"] for a in anns] == ["kept highlight"]
    s = (await client.get(f"/libraries/{library}/items", params={"q": term})).json()
    assert [i["id"] for i in s["items"]] == [keep["id"]]  # the moved PDF text is searchable


async def test_dedupe_keeps_the_duplicates_pdf_and_annotations(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    keep = await _add(client, library, title="Reefs", DOI="10.1/x")
    dup = await _add(client, library, title="Reefs", DOI="10.1/X")
    att = await _attach_annotated_pdf(client, dup["id"], "zooxanthellae bleaching")

    assert (await client.post(f"/libraries/{library}/dedupe")).json()["merged"] == 1
    await _assert_pdf_moved(client, library, keep, att, "zooxanthellae")


async def test_merge_endpoint_keeps_the_duplicates_pdf(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    keep = await _add(client, library, title="Keep")
    dup = await _add(client, library, title="Dup")
    att = await _attach_annotated_pdf(client, dup["id"], "scleractinia spawning")

    r = await client.post(f"/items/{keep['id']}/merge/{dup['id']}")
    assert r.status_code == 200
    assert [a["id"] for a in r.json()["attachments"]] == [att]
    await _assert_pdf_moved(client, library, keep, att, "scleractinia")


async def test_same_title_and_year_but_different_dois_are_not_merged(client, library):
    year = {"date-parts": [[2020]]}
    await _add(client, library, title="Editorial", DOI="10.1/a", issued=year)
    await _add(client, library, title="Editorial", DOI="10.2/b", issued=year)

    assert (await client.post(f"/libraries/{library}/dedupe")).json()["merged"] == 0
    assert len(await _items(client, library)) == 2


async def test_same_title_without_a_year_is_not_merged(client, library):
    await _add(client, library, title="Editorial")
    await _add(client, library, title="Editorial")

    assert (await client.post(f"/libraries/{library}/dedupe")).json()["merged"] == 0


async def test_a_doi_less_copy_does_not_bridge_two_different_dois(client, library):
    """A (no DOI) matches B (DOI a) on title; C (DOI b) must not then join them via A."""
    year = {"date-parts": [[2020]]}
    a = await _add(client, library, title="Editorial", issued=year)
    b = await _add(client, library, title="Editorial", DOI="10.1/a", issued=year)
    c = await _add(client, library, title="Editorial", DOI="10.2/b", issued=year)

    r = (await client.post(f"/libraries/{library}/dedupe")).json()
    assert r["groups"] == [{"kept": a["id"], "merged": [b["id"]], "matched_on": "title"}]
    assert {i["id"] for i in await _items(client, library)} == {a["id"], c["id"]}
