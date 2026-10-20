"""Phase 3b: extract PDF text, cache it on the Attachment, and fold it into the
owning item's full-text index so the PDF body is searchable via ?q=."""
import fitz  # PyMuPDF

from app.pdf import extract_pdf_text


def _make_pdf(*lines: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "\n".join(lines))
    data = doc.tobytes()
    doc.close()
    return data


async def _add(client, library, **csl):
    csl.setdefault("type", "document")
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    return r.json()


def test_extract_pdf_text_roundtrip():
    out = extract_pdf_text(_make_pdf("Photosynthesis in coral reefs"))
    assert "Photosynthesis" in out
    assert "coral" in out


def test_extract_pdf_text_handles_garbage():
    # Non-PDF bytes must not raise; just yield empty text.
    assert extract_pdf_text(b"not a pdf at all") == ""


async def test_uploaded_pdf_text_is_searchable(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = await _add(client, library, title="Reef paper")

    pdf = _make_pdf("Bleaching events devastate zooxanthellae populations")
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 201

    # A term that appears only in the PDF body (not the metadata) is now findable.
    s = await client.get(f"/libraries/{library}/items", params={"q": "zooxanthellae"})
    assert s.json()["total"] == 1
    assert s.json()["items"][0]["id"] == item["id"]


async def test_metadata_edit_keeps_pdf_text_searchable(client, library, monkeypatch):
    """Editing item metadata must not clobber the indexed PDF body."""
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = await _add(client, library, title="Reef paper")
    pdf = _make_pdf("unique term scleractinia present")
    await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )

    # Edit metadata (bumps version, re-denormalizes).
    await client.patch(
        f"/items/{item['id']}",
        json={"version": 1, "csl_json": {"type": "document", "title": "Reef paper v2"}},
    )

    s = await client.get(f"/libraries/{library}/items", params={"q": "scleractinia"})
    assert s.json()["total"] == 1


async def test_reextract_endpoint_refreshes_index(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = await _add(client, library, title="Reef paper")
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", _make_pdf("original alpha content"), "application/pdf")},
    )
    att_id = r.json()["id"]

    # Storage now returns a different PDF; re-extract should re-index it.
    monkeypatch.setattr(
        "app.routers.attachments.download_bytes",
        lambda *a, **k: _make_pdf("revised beta content"),
    )
    re = await client.post(f"/attachments/{att_id}/extract-text")
    assert re.status_code == 200

    assert (await client.get(f"/libraries/{library}/items", params={"q": "beta"})).json()[
        "total"
    ] == 1


# ── Reading an attachment's extracted text (agents read papers through this) ──────────


async def _attach(client, library, monkeypatch, *lines):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = await _add(client, library, title="Reef paper")
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", _make_pdf(*lines), "application/pdf")},
    )
    return r.json()["id"]


async def test_attachment_text_is_readable(client, library, monkeypatch):
    att = await _attach(client, library, monkeypatch, "Symbiodinium thrive in warm water")
    r = await client.get(f"/attachments/{att}/text")
    assert r.status_code == 200
    body = r.json()
    assert "Symbiodinium" in body["text"]
    assert body["offset"] == 0
    assert body["total_chars"] == len(body["text"])
    assert body["next_offset"] is None


async def test_attachment_text_pages(client, library, monkeypatch):
    att = await _attach(client, library, monkeypatch, "abcdefghijklmnopqrstuvwxyz")
    full = (await client.get(f"/attachments/{att}/text")).json()["text"]
    first = (await client.get(f"/attachments/{att}/text", params={"limit": 10})).json()
    assert first["text"] == full[:10]
    assert first["next_offset"] == 10
    rest = (
        await client.get(f"/attachments/{att}/text", params={"offset": 10, "limit": 1000})
    ).json()
    assert first["text"] + rest["text"] == full
    assert rest["next_offset"] is None


async def test_attachment_text_needs_library_access(
    client, library, monkeypatch, make_user, login
):
    att = await _attach(client, library, monkeypatch, "secret findings")
    login(await make_user("eve@e4e.local", "Eve"))
    assert (await client.get(f"/attachments/{att}/text")).status_code == 404


async def test_attachment_without_text(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = await _add(client, library, title="Scan")
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("scan.png", b"\x89PNG not text", "image/png")},
    )
    body = (await client.get(f"/attachments/{r.json()['id']}/text")).json()
    assert body["text"] == ""
    assert body["total_chars"] == 0
