"""Find a legal open-access PDF online (Unpaywall by DOI, then arXiv) and attach it.

No network: the HTTP client is an httpx.MockTransport and DNS resolution is stubbed."""
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
