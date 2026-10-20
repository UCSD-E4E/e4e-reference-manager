"""One-time PDF upload links: someone with edit access asks for a short-lived URL for a
reference, and anything holding that URL (an agent running `curl -F file=@paper.pdf`)
can upload exactly one PDF to it, without a session or API token. The file goes from
disk to the server without passing through the model."""
from datetime import datetime, timedelta, timezone

import fitz  # PyMuPDF
import pytest
from sqlalchemy import select

from app.models import PdfUploadLink

BASE = "http://localhost:8000"  # Settings.app_base_url default


def _pdf(text="Paywalled paper body") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


@pytest.fixture(autouse=True)
def no_storage(monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)


async def _item(client, library):
    r = await client.post(
        f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": "Paper"}}
    )
    return r.json()["id"]


async def _link(client, item_id):
    r = await client.post(f"/items/{item_id}/upload-links")
    assert r.status_code == 201, r.text
    return r.json()


def _path(url: str) -> str:
    assert url.startswith(BASE + "/uploads/")
    return url[len(BASE):]


async def _upload(client, url, data=None, filename="paper.pdf"):
    return await client.post(
        _path(url),
        files={"file": (filename, data if data is not None else _pdf(), "application/pdf")},
    )


async def test_create_link(client, library):
    out = await _link(client, await _item(client, library))
    expires = datetime.fromisoformat(out["expires_at"])
    assert timedelta(minutes=14) < expires - datetime.now(timezone.utc) <= timedelta(minutes=15)
    assert out["curl"] == f"curl -fsS -F file=@paper.pdf '{out['url']}'"


async def test_upload_attaches_the_pdf(client, library):
    item_id = await _item(client, library)
    link = await _link(client, item_id)
    r = await _upload(client, link["url"], filename="hamilton2021.pdf")
    assert r.status_code == 201
    assert r.json()["filename"] == "hamilton2021.pdf"

    item = (await client.get(f"/items/{item_id}")).json()
    assert [a["filename"] for a in item["attachments"]] == ["hamilton2021.pdf"]
    text = (await client.get(f"/attachments/{r.json()['id']}/text")).json()["text"]
    assert "Paywalled paper body" in text  # extracted like any upload


async def test_upload_needs_no_session(client, library, login):
    link = await _link(client, await _item(client, library))
    login(None)  # no logged-in user: the link itself is the credential
    assert (await _upload(client, link["url"])).status_code == 201


async def test_link_works_only_once(client, library):
    link = await _link(client, await _item(client, library))
    assert (await _upload(client, link["url"])).status_code == 201
    again = await _upload(client, link["url"], _pdf("second"))
    assert again.status_code == 410


async def test_expired_link_is_refused(client, library, db):
    link = await _link(client, await _item(client, library))
    row = (await db.execute(select(PdfUploadLink))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    assert (await _upload(client, link["url"])).status_code == 410


async def test_unknown_link_is_404(client):
    r = await client.post(
        "/uploads/not-a-real-token", files={"file": ("p.pdf", _pdf(), "application/pdf")}
    )
    assert r.status_code == 404


@pytest.mark.parametrize("data", [b"<html>not a pdf</html>", b""])
async def test_only_pdfs_are_accepted_and_the_link_survives(client, library, data):
    link = await _link(client, await _item(client, library))
    assert (await _upload(client, link["url"], data)).status_code == 400
    assert (await _upload(client, link["url"])).status_code == 201  # not used up


async def test_size_cap(client, library, monkeypatch):
    monkeypatch.setattr("app.upload_limits.MAX_PDF_BYTES", 100)
    link = await _link(client, await _item(client, library))
    assert (await _upload(client, link["url"], _pdf() + b"x" * 200)).status_code == 413


async def test_only_editors_can_create_links(client, library, make_user, login):
    item_id = await _item(client, library)
    login(await make_user("eve@e4e.local", "Eve"))
    assert (await client.post(f"/items/{item_id}/upload-links")).status_code in (403, 404)


async def test_link_dies_if_its_creator_loses_access(client, library, make_user, login, db):
    """Access is re-checked at upload time, not just when the link was made."""
    group = (await client.post("/groups", json={"slug": "lab", "name": "Lab"})).json()
    bob = await make_user("bob@e4e.local", "Bob")
    await client.post(f"/groups/{group['id']}/members", json={"email": bob.email})
    await client.post(
        f"/libraries/{library}/shares", json={"group_id": group["id"], "access_level": "edit"}
    )
    item_id = await _item(client, library)
    login(bob)
    link = await _link(client, item_id)

    from app.models import LibraryShare

    share = (await db.execute(select(LibraryShare))).scalar_one()
    share.access_level = "view"
    await db.commit()
    assert (await _upload(client, link["url"])).status_code == 403


async def test_link_dies_with_a_trashed_reference(client, library):
    item_id = await _item(client, library)
    link = await _link(client, item_id)
    await client.delete(f"/items/{item_id}")
    assert (await _upload(client, link["url"])).status_code == 410


async def test_only_a_hash_of_the_secret_is_stored(client, library, db):
    link = await _link(client, await _item(client, library))
    secret = link["url"].rsplit("/", 1)[1]
    row = (await db.execute(select(PdfUploadLink))).scalar_one()
    assert secret not in row.token_hash
