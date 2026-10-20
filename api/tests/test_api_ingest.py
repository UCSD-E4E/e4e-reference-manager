"""Ingestion: identifier import + dedup, from-PDF creation, and merge.

External services (translation-server, GROBID) and object storage are monkeypatched.
"""
import pytest


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


async def test_ingest_dedups_by_title_when_there_is_no_doi(client, library, monkeypatch):
    """Books (ISBN) and many URLs resolve without a DOI: retrying must not add a copy."""
    book = {"type": "book", "title": "Coral Reef Ecology", "issued": {"date-parts": [[2011]]}}

    async def fake_fetch(query):
        return [dict(book)]

    monkeypatch.setattr("app.routers.ingest.fetch_csl", fake_fetch)
    first = (await client.post(f"/libraries/{library}/ingest", json={"query": "isbn:1"})).json()
    again = (await client.post(f"/libraries/{library}/ingest", json={"query": "isbn:1"})).json()
    assert first["results"][0]["status"] == "created"
    assert again["results"][0]["status"] == "duplicate"
    assert again["results"][0]["item_id"] == first["results"][0]["item_id"]
    total = (await client.get(f"/libraries/{library}/items")).json()["total"]
    assert total == 1


async def test_ingest_dedups_within_one_batch(client, library, monkeypatch):
    async def fake_fetch(query):
        return [dict(CSL_PAPER), dict(CSL_PAPER)]

    monkeypatch.setattr("app.routers.ingest.fetch_csl", fake_fetch)
    r = (await client.post(f"/libraries/{library}/ingest", json={"query": "x"})).json()
    assert [x["status"] for x in r["results"]] == ["created", "duplicate"]


async def test_ingest_same_title_and_year_with_another_doi_is_created(
    client, library, monkeypatch
):
    async def fake_fetch(query):
        return [dict(CSL_PAPER), dict(CSL_PAPER, DOI="10.2/other")]

    monkeypatch.setattr("app.routers.ingest.fetch_csl", fake_fetch)
    r = (await client.post(f"/libraries/{library}/ingest", json={"query": "x"})).json()
    assert [x["status"] for x in r["results"]] == ["created", "created"]


async def test_unreachable_translation_server_names_the_error(monkeypatch):
    """Timeouts stringify to "": the message must still say what went wrong."""
    import httpx

    from app import ingest

    class Timeout:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(ingest.httpx, "AsyncClient", Timeout)
    with pytest.raises(ingest.IngestError, match="ReadTimeout"):
        await ingest.fetch_csl("10.1/x")
