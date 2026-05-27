"""Phase 4: PDF highlight/comment annotations anchored to an attachment.

An annotation stores a page + normalized rectangles (0..1, resilient to zoom/width) plus
the quoted text and an optional comment. RBAC mirrors notes: view to read, edit to
create, author-or-manager to modify/delete. Storage is monkeypatched (no SeaweedFS)."""


async def _item_with_attachment(client, library, monkeypatch):
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    item = (
        await client.post(
            f"/libraries/{library}/items",
            json={"csl_json": {"type": "document", "title": "Paper"}},
        )
    ).json()
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 201
    return item, r.json()["id"]


async def test_create_and_list_annotation(client, library, monkeypatch):
    _, att = await _item_with_attachment(client, library, monkeypatch)
    body = {
        "page": 2,
        "rects": [{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}],
        "color": "#ffd54f",
        "quote": "important sentence",
        "comment": "key claim",
    }
    r = await client.post(f"/attachments/{att}/annotations", json=body)
    assert r.status_code == 201
    ann = r.json()
    assert ann["page"] == 2
    assert ann["quote"] == "important sentence"
    assert ann["rects"][0]["w"] == 0.3
    assert ann["author_id"] is not None

    lst = await client.get(f"/attachments/{att}/annotations")
    assert lst.status_code == 200
    assert len(lst.json()) == 1
    assert lst.json()[0]["comment"] == "key claim"


async def test_update_annotation(client, library, monkeypatch):
    _, att = await _item_with_attachment(client, library, monkeypatch)
    ann = (
        await client.post(
            f"/attachments/{att}/annotations", json={"page": 1, "rects": [], "comment": "x"}
        )
    ).json()
    up = await client.patch(
        f"/annotations/{ann['id']}", json={"color": "#90caf9", "comment": "updated"}
    )
    assert up.status_code == 200
    assert up.json()["color"] == "#90caf9"
    assert up.json()["comment"] == "updated"


async def test_delete_annotation(client, library, monkeypatch):
    _, att = await _item_with_attachment(client, library, monkeypatch)
    ann = (
        await client.post(f"/attachments/{att}/annotations", json={"page": 1, "rects": []})
    ).json()
    assert (await client.delete(f"/annotations/{ann['id']}")).status_code == 204
    assert (await client.get(f"/attachments/{att}/annotations")).json() == []


async def test_annotation_is_audited(client, library, monkeypatch):
    _, att = await _item_with_attachment(client, library, monkeypatch)
    await client.post(f"/attachments/{att}/annotations", json={"page": 1, "rects": []})
    acts = (await client.get(f"/libraries/{library}/activity")).json()
    assert any(e["entity_type"] == "annotation" and e["operation"] == "create" for e in acts)


async def test_annotation_rbac(client, make_user, login, monkeypatch):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")
    carol = await make_user("carol@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "team", "name": "Team"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "Alice"})).json()["id"]
    _, att = await _item_with_attachment(client, lib, monkeypatch)
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "view"}
    )

    # No access at all -> 404 (existence hidden)
    login(carol)
    assert (await client.get(f"/attachments/{att}/annotations")).status_code == 404

    # View-only (bob via share): can read, cannot create
    login(bob)
    assert (await client.get(f"/attachments/{att}/annotations")).status_code == 200
    denied = await client.post(f"/attachments/{att}/annotations", json={"page": 1, "rects": []})
    assert denied.status_code == 403
