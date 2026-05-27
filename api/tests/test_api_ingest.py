"""Ingestion: identifier import + dedup, from-PDF creation, and merge.

External services (translation-server, GROBID) and object storage are monkeypatched.
"""

CSL_PAPER = {
    "type": "article-journal",
    "title": "Reef Monitoring with Drones",
    "DOI": "10.1/Z",
    "author": [{"family": "Ng", "given": "A"}],
    "issued": {"date-parts": [[2020]]},
}


async def test_ingest_creates_then_dedups(client, library, monkeypatch):
    async def fake_fetch(query):
        return [dict(CSL_PAPER)]

    monkeypatch.setattr("app.routers.ingest.fetch_csl", fake_fetch)

    first = await client.post(f"/libraries/{library}/ingest", json={"query": "10.1/Z"})
    assert first.status_code == 200
    assert first.json()["results"][0]["status"] == "created"

    again = await client.post(f"/libraries/{library}/ingest", json={"query": "10.1/Z"})
    assert again.json()["results"][0]["status"] == "duplicate"  # same DOI


async def test_create_item_from_pdf(client, library, monkeypatch):
    async def fake_extract(pdf):
        return {"type": "article-journal", "title": "From PDF", "DOI": "10.1/p"}

    monkeypatch.setattr("app.routers.ingest.extract_header_csl", fake_extract)
    monkeypatch.setattr("app.routers.ingest.upload_bytes", lambda *a, **k: None)

    r = await client.post(
        f"/libraries/{library}/items/from-pdf",
        files={"file": ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 201
    assert r.json()["title"] == "From PDF"
    assert r.json()["attachments"][0]["filename"] == "paper.pdf"


async def test_merge_moves_notes_and_deletes_other(client, library):
    keep = (
        await client.post(
            f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": "Keep"}}
        )
    ).json()
    dup = (
        await client.post(
            f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": "Dup"}}
        )
    ).json()
    await client.post(f"/items/{dup['id']}/notes", json={"body": "note on dup"})

    merged = await client.post(f"/items/{keep['id']}/merge/{dup['id']}")
    assert merged.status_code == 200

    assert (await client.get(f"/items/{dup['id']}")).status_code == 404  # dup removed
    notes = (await client.get(f"/items/{keep['id']}/notes")).json()
    assert [n["body"] for n in notes] == ["note on dup"]  # note moved


async def test_ingest_translation_server_error_is_502(client, library, monkeypatch):
    from app.ingest import IngestError

    async def boom(query):
        raise IngestError("nope")

    monkeypatch.setattr("app.routers.ingest.fetch_csl", boom)
    r = await client.post(f"/libraries/{library}/ingest", json={"query": "10.1/Z"})
    assert r.status_code == 502
