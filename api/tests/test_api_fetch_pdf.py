"""Find a legal open-access PDF online (Unpaywall by DOI, then arXiv) and attach it.

No network: the HTTP client is an httpx.MockTransport and DNS resolution is stubbed."""
import hashlib

import fitz  # PyMuPDF
import httpx
import pytest

import app.oa_pdf as oa


def _pdf(text="Open access paper body") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


PDF = _pdf()


def _routes(routes: dict):
    """Serve `routes` {url: (status, body, headers)}; anything else 404s."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, body, headers = routes.get(str(request.url), (404, b"", {}))
        return httpx.Response(status, content=body, headers=headers)

    return httpx.MockTransport(handler)


@pytest.fixture
def net(monkeypatch):
    """Install a fake internet; returns a dict to fill with routes."""
    routes: dict = {}
    monkeypatch.setattr(oa, "_transport", lambda: _routes(routes))
    monkeypatch.setattr(oa, "_resolves_to_public", lambda host: host != "internal.example")
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    return routes


def _unpaywall(doi, *urls):
    import json

    locs = [{"url_for_pdf": u} for u in urls]
    body = {"best_oa_location": locs[0] if locs else None, "oa_locations": locs}
    return {oa.unpaywall_url(doi): (200, json.dumps(body).encode(), {})}


# ---------- candidate discovery + download (unit) ----------


async def test_unpaywall_then_arxiv_candidates_in_order(net):
    net.update(_unpaywall("10.1/x", "https://pub.example/x.pdf", "https://repo.example/x.pdf"))
    csl = {"DOI": "10.1/X", "URL": "https://arxiv.org/abs/2101.00001"}
    got = await oa.find_candidates(csl)
    assert got == [
        ("unpaywall", "https://pub.example/x.pdf"),
        ("unpaywall", "https://repo.example/x.pdf"),
        ("arxiv", "https://arxiv.org/pdf/2101.00001"),
    ]


async def test_download_rejects_html_landing_pages(net):
    net["https://pub.example/x.pdf"] = (200, b"<html>Please log in</html>", {})
    assert await oa.download_pdf("https://pub.example/x.pdf") is None


async def test_download_follows_redirects_but_never_to_internal_hosts(net):
    net["https://pub.example/a"] = (302, b"", {"location": "https://cdn.example/a.pdf"})
    net["https://cdn.example/a.pdf"] = (200, PDF, {})
    assert await oa.download_pdf("https://pub.example/a") == PDF

    net["https://pub.example/evil"] = (302, b"", {"location": "http://internal.example/x"})
    assert await oa.download_pdf("https://pub.example/evil") is None


async def test_download_rejects_non_http_and_oversize(net, monkeypatch):
    assert await oa.download_pdf("file:///etc/passwd") is None
    monkeypatch.setattr(oa, "MAX_PDF_BYTES", 10)
    net["https://pub.example/big.pdf"] = (200, PDF, {})
    assert await oa.download_pdf("https://pub.example/big.pdf") is None


# ---------- endpoint ----------


async def _add(client, library, **csl):
    csl.setdefault("type", "article")
    csl.setdefault("title", "A paper")
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    return r.json()


async def test_fetch_attaches_the_first_real_pdf(client, library, net):
    net.update(_unpaywall("10.1/x", "https://pub.example/landing", "https://repo.example/x.pdf"))
    net["https://pub.example/landing"] = (200, b"<html>landing</html>", {})
    net["https://repo.example/x.pdf"] = (200, PDF, {})
    item = await _add(client, library, DOI="10.1/x")

    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 201
    body = r.json()
    assert body["source"] == "unpaywall" and body["url"] == "https://repo.example/x.pdf"
    assert body["attachment"]["filename"].endswith(".pdf")

    full = (await client.get(f"/items/{item['id']}")).json()
    assert len(full["attachments"]) == 1
    # its text is extracted, so the PDF is searchable like an upload
    hits = (await client.get(f"/libraries/{library}/items", params={"q": "body"})).json()
    assert hits["total"] == 1


async def test_fetch_falls_back_to_arxiv(client, library, net):
    net["https://arxiv.org/pdf/2101.00001"] = (200, PDF, {})
    item = await _add(client, library, URL="https://arxiv.org/abs/2101.00001")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 201
    assert r.json()["source"] == "arxiv"


async def test_fetch_twice_does_not_attach_the_same_pdf_again(client, library, net):
    net["https://arxiv.org/pdf/2101.00001"] = (200, PDF, {})
    item = await _add(client, library, URL="https://arxiv.org/abs/2101.00001")
    await client.post(f"/items/{item['id']}/fetch-pdf")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 200  # already attached
    assert len((await client.get(f"/items/{item['id']}")).json()["attachments"]) == 1


async def test_nothing_open_access_is_a_clear_404(client, library, net):
    net.update(_unpaywall("10.1/paywalled"))
    item = await _add(client, library, DOI="10.1/paywalled")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 404
    assert "open-access" in r.json()["detail"]


async def test_arxiv_doi_form_is_recognised(net):
    got = await oa.find_candidates({"DOI": "10.48550/arXiv.2101.00001"})
    assert ("arxiv", "https://arxiv.org/pdf/2101.00001") in got


# ---------- library-wide fetch ----------


def _counts(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "next_after"}


async def test_fetch_pdfs_for_whole_library(client, library, net):
    net["https://arxiv.org/pdf/2101.00001"] = (200, PDF, {})
    net.update(_unpaywall("10.1/paywalled"))  # no OA locations
    await _add(client, library, URL="https://arxiv.org/abs/2101.00001", title="Has OA")
    await _add(client, library, DOI="10.1/paywalled", title="No OA")
    await _add(client, library, title="A book with no identifiers")

    r = await client.post(f"/libraries/{library}/fetch-pdfs")
    assert r.status_code == 200
    body = r.json()
    assert body["next_after"] is None  # everything fit in one batch
    assert _counts(body) == {
        "checked": 3,
        "attached": 1,
        "already_had_pdf": 0,
        "not_found": 1,
        "no_identifier": 1,  # nothing to look up isn't the same as paywalled
        "failed": 0,
    }


async def test_fetch_pdfs_skips_items_that_already_have_a_pdf(client, library, net):
    net["https://arxiv.org/pdf/2101.00001"] = (200, PDF, {})
    await _add(client, library, URL="https://arxiv.org/abs/2101.00001", title="Has OA")

    first = await client.post(f"/libraries/{library}/fetch-pdfs")
    assert first.json()["attached"] == 1
    # Second pass: the PDF is already attached, so it is counted as skipped, not refetched.
    second = (await client.post(f"/libraries/{library}/fetch-pdfs")).json()
    assert second["already_had_pdf"] == 1 and second["attached"] == 0


async def test_fetch_pdfs_recognises_pdfs_uploaded_with_a_generic_mime_type(
    client, library, net
):
    net["https://arxiv.org/pdf/2101.00001"] = (200, PDF, {})
    item = await _add(client, library, URL="https://arxiv.org/abs/2101.00001")
    up = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("Paper.PDF", _pdf("uploaded"), "application/octet-stream")},
    )
    assert up.status_code == 201

    body = (await client.post(f"/libraries/{library}/fetch-pdfs")).json()
    assert body["already_had_pdf"] == 1 and body["attached"] == 0


async def test_fetch_pdfs_pages_through_the_library_with_a_cursor(client, library, net):
    for n in (1, 2, 3):
        url = f"https://arxiv.org/pdf/2101.0000{n}"
        net[url] = (200, _pdf(f"paper {n}"), {})
        await _add(client, library, URL=f"https://arxiv.org/abs/2101.0000{n}")

    seen, after = 0, None
    for _ in range(5):
        params = {"limit": 2, **({"after": after} if after else {})}
        body = (await client.post(f"/libraries/{library}/fetch-pdfs", params=params)).json()
        assert body["checked"] <= 2
        seen += body["checked"]
        after = body["next_after"]
        if after is None:
            break
    assert seen == 3 and after is None
    items = (await client.get(f"/libraries/{library}/items")).json()["items"]
    assert all(len(it["attachments"]) == 1 for it in items)


async def test_fetch_pdfs_one_failure_does_not_abort_the_batch(
    client, library, net, monkeypatch
):
    good = _pdf("good")  # build once: PyMuPDF output isn't byte-identical across calls
    net["https://arxiv.org/pdf/2101.00001"] = (200, good, {})
    net["https://arxiv.org/pdf/2101.00002"] = (200, _pdf("bad"), {})
    await _add(client, library, URL="https://arxiv.org/abs/2101.00001")
    await _add(client, library, URL="https://arxiv.org/abs/2101.00002")

    good_sha = hashlib.sha256(good).hexdigest()

    def flaky_upload(key, *a, **k):
        if not key.endswith(good_sha):
            raise RuntimeError("object storage hiccup")

    monkeypatch.setattr("app.routers.attachments.upload_bytes", flaky_upload)
    body = (await client.post(f"/libraries/{library}/fetch-pdfs")).json()
    assert body["attached"] == 1 and body["failed"] == 1 and body["checked"] == 2


async def test_fetch_pdfs_requires_edit_access(client, make_user, login, net):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "g", "name": "G"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "Alice lib"})).json()["id"]
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "view"}
    )

    login(bob)
    r = await client.post(f"/libraries/{lib}/fetch-pdfs")
    assert r.status_code == 403
