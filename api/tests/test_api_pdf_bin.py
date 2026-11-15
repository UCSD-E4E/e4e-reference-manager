"""A reference's PDF recycle bin: anyone with edit access can move a PDF there (with a
reason) or restore it; only a person signed in to the web app can delete one for good.
A binned PDF stops counting as the reference's PDF (search, has_pdf, fetching)."""
from datetime import datetime, timedelta, timezone

import fitz  # PyMuPDF
import httpx
import pytest

from app.auth import get_auth_context, new_api_token
from app.main import app


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def storage(monkeypatch):
    """Object storage as a dict, so tests can see what was stored and deleted."""
    objects: dict[str, bytes] = {}
    monkeypatch.setattr(
        "app.routers.attachments.upload_bytes", lambda key, data, _t: objects.__setitem__(key, data)
    )
    monkeypatch.setattr("app.routers.attachments.delete_object", objects.pop)
    return objects


async def _paper(client, library, title="Reef paper", **csl):
    r = await client.post(
        f"/libraries/{library}/items", json={"csl_json": {"type": "document", "title": title, **csl}}
    )
    assert r.status_code == 201
    return r.json()


async def _upload(client, item_id, text="Bleaching devastates zooxanthellae"):
    r = await client.post(
        f"/items/{item_id}/attachments",
        files={"file": ("p.pdf", _make_pdf(text), "application/pdf")},
    )
    assert r.status_code == 201
    return r.json()


async def _archive(client, att_id, reason="Wrong paper: this is the supplement"):
    return await client.post(f"/attachments/{att_id}/archive", json={"reason": reason})


async def test_archive_moves_a_pdf_to_the_bin(client, library, storage, alice):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])

    r = await _archive(client, att["id"])
    assert r.status_code == 200
    binned = r.json()
    assert binned["archive_reason"] == "Wrong paper: this is the supplement"
    assert binned["archived_at"]
    assert binned["archived_by"]["email"] == alice.email

    got = (await client.get(f"/items/{item['id']}")).json()
    assert got["attachments"] == []
    assert [a["id"] for a in got["archived_attachments"]] == [att["id"]]
    assert storage  # the file is kept


async def test_a_binned_pdf_leaves_search(client, library, storage):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    q = {"q": "zooxanthellae"}
    assert (await client.get(f"/libraries/{library}/items", params=q)).json()["total"] == 1

    await _archive(client, att["id"])
    assert (await client.get(f"/libraries/{library}/items", params=q)).json()["total"] == 0

    assert (await client.post(f"/attachments/{att['id']}/restore")).status_code == 200
    assert (await client.get(f"/libraries/{library}/items", params=q)).json()["total"] == 1


async def test_restore_brings_it_back(client, library, storage):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    await _archive(client, att["id"])

    r = await client.post(f"/attachments/{att['id']}/restore")
    assert r.status_code == 200
    assert r.json()["archived_at"] is None and r.json()["archive_reason"] is None
    got = (await client.get(f"/items/{item['id']}")).json()
    assert [a["id"] for a in got["attachments"]] == [att["id"]]
    assert got["archived_attachments"] == []


@pytest.mark.parametrize("reason", ["", "   "])
async def test_archive_needs_a_reason(client, library, storage, reason):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    assert (await _archive(client, att["id"], reason)).status_code == 422


async def test_archive_and_restore_say_when_there_is_nothing_to_do(client, library, storage):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    assert (await client.post(f"/attachments/{att['id']}/restore")).status_code == 409
    await _archive(client, att["id"])
    assert (await _archive(client, att["id"])).status_code == 409


async def test_archive_needs_edit_access(client, library, storage, make_user, login):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    group = (await client.post("/groups", json={"slug": "lab", "name": "Lab"})).json()
    bob = await make_user("bob@e4e.local", "Bob")
    await client.post(f"/groups/{group['id']}/members", json={"email": bob.email})
    await client.post(
        f"/libraries/{library}/shares", json={"group_id": group["id"], "access_level": "view"}
    )
    login(bob)
    assert (await _archive(client, att["id"])).status_code == 403
    assert (await client.delete(f"/attachments/{att['id']}")).status_code == 403


async def test_archive_and_restore_are_in_the_history(client, library, storage):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    await _archive(client, att["id"])
    await client.post(f"/attachments/{att['id']}/restore")
    r = await client.get(f"/libraries/{library}/activity")
    assert r.status_code == 200, r.text
    summaries = [e["summary"] for e in r.json()]
    assert any("recycle bin" in s and "supplement" in s for s in summaries)
    assert any("Restored" in s and "p.pdf" in s for s in summaries)


async def test_delete_forever_needs_the_pdf_in_the_bin(client, library, storage):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    assert (await client.delete(f"/attachments/{att['id']}")).status_code == 409


async def test_delete_forever_removes_the_pdf_and_its_file(client, library, storage):
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    await _archive(client, att["id"])

    assert (await client.delete(f"/attachments/{att['id']}")).status_code == 204
    got = (await client.get(f"/items/{item['id']}")).json()
    assert got["attachments"] == [] and got["archived_attachments"] == []
    assert storage == {}
    assert (await client.get(f"/attachments/{att['id']}/text")).status_code == 404


async def test_delete_forever_keeps_a_file_another_attachment_shares(client, library, storage):
    item = await _paper(client, library)
    first = await _upload(client, item["id"])
    second = await _upload(client, item["id"])  # same bytes: same stored object
    await _archive(client, first["id"])
    assert (await client.delete(f"/attachments/{first['id']}")).status_code == 204
    assert len(storage) == 1
    got = (await client.get(f"/items/{item['id']}")).json()
    assert [a["id"] for a in got["attachments"]] == [second["id"]]


async def test_api_tokens_cannot_delete_forever(client, library, storage, db, alice):
    """Agents and scripts can bin and restore, but deleting for good is for people."""
    item = await _paper(client, library)
    att = await _upload(client, item["id"])
    raw, row = new_api_token(alice, "agent", datetime.now(timezone.utc) + timedelta(days=1))
    db.add(row)
    await db.commit()

    app.dependency_overrides.pop(get_auth_context, None)  # real token auth from here
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {raw}"}
    ) as agent:
        assert (await _archive(agent, att["id"])).status_code == 200
        assert (await agent.delete(f"/attachments/{att['id']}")).status_code == 403
        assert (await agent.post(f"/attachments/{att['id']}/restore")).status_code == 200


async def test_fetching_does_not_bring_back_a_binned_pdf(client, library, storage, monkeypatch):
    pdf = _make_pdf("An open-access copy that is the wrong paper")

    async def found(csl):
        return pdf, "unpaywall", "https://oa.example/wrong.pdf"

    monkeypatch.setattr("app.oa_pdf.fetch_open_access_pdf", found)
    monkeypatch.setattr("app.oa_pdf.has_identifier", lambda csl: True)
    item = await _paper(client, library, DOI="10.1/wrong")
    att = (await client.post(f"/items/{item['id']}/fetch-pdf")).json()["attachment"]
    await _archive(client, att["id"], "Unpaywall's copy is a different paper")

    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 409
    assert "Unpaywall's copy is a different paper" in r.json()["detail"]

    batch = (await client.post(f"/libraries/{library}/fetch-pdfs")).json()
    assert batch["attached"] == 0 and batch["archived"] == 1
    assert (await client.get(f"/items/{item['id']}")).json()["attachments"] == []


async def test_trash_delete_forever_is_for_people_too(client, library, db, alice):
    item = await _paper(client, library)
    assert (await client.delete(f"/items/{item['id']}")).status_code == 204
    raw, row = new_api_token(alice, "agent", datetime.now(timezone.utc) + timedelta(days=1))
    db.add(row)
    await db.commit()

    app.dependency_overrides.pop(get_auth_context, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {raw}"}
    ) as agent:
        trash = f"/libraries/{library}/trash"
        assert (await agent.delete(f"{trash}/{item['id']}")).status_code == 403
        assert (await agent.delete(trash)).status_code == 403
        assert (await agent.post(f"{trash}/{item['id']}/restore")).status_code == 200


async def test_merging_duplicates_keeps_the_recycle_bin(client, library, storage):
    keep = await _paper(client, library, title="Kept copy")
    other = await _paper(client, library, title="Other copy")
    att = await _upload(client, other["id"])
    await _archive(client, att["id"])

    r = await client.post(f"/items/{keep['id']}/merge/{other['id']}")
    assert r.status_code == 200, r.text
    assert [a["id"] for a in r.json()["archived_attachments"]] == [att["id"]]
    assert r.json()["archived_attachments"][0]["archive_reason"].startswith("Wrong paper")
