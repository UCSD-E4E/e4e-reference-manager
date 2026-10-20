"""Find a legal open-access PDF online (Unpaywall by DOI, then arXiv, then the
reference's own URL) and attach it.

No network: the HTTP client is an httpx.MockTransport and DNS resolution is stubbed.
Downloads connect to the address the stub "resolved" (FAKE_IP) with the original name in
the Host header; the mock transport maps that back to the URL the routes are keyed by.
"""
import asyncio
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


FAKE_IP = "203.0.113.10"  # what every public host "resolves" to


class Net(dict):
    """{url: (status, body, headers)} routes, plus every request the server saw."""

    def __init__(self):
        super().__init__()
        self.seen: list[httpx.Request] = []

    def urls(self) -> list[str]:
        return [_logical_url(r) for r in self.seen]


def _logical_url(request: httpx.Request) -> str:
    """The URL as the caller meant it: the Host header names the server."""
    return str(request.url.copy_with(netloc=request.headers["host"].encode()))


def _routes(routes: Net):
    """Serve `routes`; anything else 404s."""

    def handler(request: httpx.Request) -> httpx.Response:
        routes.seen.append(request)
        status, body, headers = routes.get(_logical_url(request), (404, b"", {}))
        return httpx.Response(status, content=body, headers=headers)

    return httpx.MockTransport(handler)


@pytest.fixture
def net(monkeypatch):
    """Install a fake internet; returns a dict to fill with routes."""
    routes = Net()
    monkeypatch.setattr(oa, "_transport", lambda: _routes(routes))
    monkeypatch.setattr(
        oa, "_public_address", lambda host: None if host == "internal.example" else FAKE_IP
    )
    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    return routes


def _streaming(body):
    """A transport whose every response streams `body` (an async iterator factory)."""
    return lambda: httpx.MockTransport(lambda request: httpx.Response(200, content=body()))


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
    net["http://internal.example/x"] = (200, PDF, {})
    assert await oa.download_pdf("https://pub.example/evil") is None
    assert "http://internal.example/x" not in net.urls()


async def test_download_connects_to_the_address_it_validated(net):
    """No second DNS lookup (rebinding): the request goes to the checked IP, while the
    Host header and TLS (SNI + certificate check) still use the original name."""
    net["https://pub.example/x.pdf"] = (200, PDF, {})
    assert await oa.download_pdf("https://pub.example/x.pdf") == PDF
    (req,) = net.seen
    assert req.url.host == FAKE_IP
    assert req.headers["host"] == "pub.example"
    assert req.extensions["sni_hostname"] == "pub.example"


async def test_download_rejects_html_without_reading_all_of_it(net, monkeypatch):
    sent = []

    async def endless_html():
        for _ in range(1000):
            sent.append(1)
            yield b"<html>" + b"x" * 1000

    monkeypatch.setattr(oa, "_transport", _streaming(endless_html))
    assert await oa.download_pdf("https://pub.example/x.pdf") is None
    assert len(sent) < 5


async def test_download_has_an_overall_deadline(net, monkeypatch):
    async def trickle():
        yield b"%PDF-1.4"
        while True:  # each read is quick, so per-read timeouts never fire
            await asyncio.sleep(0.01)
            yield b" "

    monkeypatch.setattr(oa, "DOWNLOAD_DEADLINE", 0.2)
    monkeypatch.setattr(oa, "_transport", _streaming(trickle))
    assert await asyncio.wait_for(oa.download_pdf("https://pub.example/x.pdf"), 5) is None


@pytest.mark.parametrize(
    "addrs, ok",
    [
        (["93.184.216.34"], True),
        (["93.184.216.34", "10.0.0.5"], False),  # one private answer is enough to refuse
        (["127.0.0.1"], False),
        (["169.254.169.254"], False),  # cloud metadata
        (["::ffff:127.0.0.1"], False),  # IPv4-mapped loopback
        (["64:ff9b::a00:1"], False),  # NAT64 of 10.0.0.1
        (["fe80::1%eth0"], False),
        ([], False),
    ],
)
def test_public_address_checks_every_answer(monkeypatch, addrs, ok):
    from app import netguard

    def fake_getaddrinfo(host, port, *a, **k):
        return [(0, 0, 0, "", (addr, 0)) for addr in addrs]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake_getaddrinfo)
    assert (oa._public_address("pub.example") is not None) is ok
    assert bool(netguard.public_addresses("pub.example")) is ok


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


async def test_fetch_pdfs_failure_in_a_full_batch_still_returns_a_cursor(
    client, library, net, monkeypatch
):
    # A full batch (checked == limit) is the case that has to compute next_after, after
    # the failed item's rollback has expired every loaded object.
    for n in (1, 2):
        net[f"https://arxiv.org/pdf/2101.0000{n}"] = (200, _pdf(f"paper {n}"), {})
        await _add(client, library, URL=f"https://arxiv.org/abs/2101.0000{n}")

    def broken_upload(*a, **k):
        raise RuntimeError("object storage hiccup")

    monkeypatch.setattr("app.routers.attachments.upload_bytes", broken_upload)
    r = await client.post(f"/libraries/{library}/fetch-pdfs", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["failed"] == 2 and body["checked"] == 2
    assert body["next_after"] is not None


# ---------- the reference's own URL as a last resort ----------


async def test_reference_url_is_the_last_candidate(net):
    net.update(_unpaywall("10.1/x", "https://pub.example/x.pdf"))
    csl = {"DOI": "10.1/x", "URL": "https://lab.example/hamilton2021.pdf"}
    assert await oa.find_candidates(csl) == [
        ("unpaywall", "https://pub.example/x.pdf"),
        ("url", "https://lab.example/hamilton2021.pdf"),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2101.00001",  # covered by the arXiv candidate
        "https://doi.org/10.1/x",  # covered by Unpaywall
        "https://pub.example/x.pdf",  # already an Unpaywall candidate
        "ftp://lab.example/x.pdf",
        "not a url",
    ],
)
async def test_reference_url_is_skipped_when_redundant_or_unusable(net, url):
    net.update(_unpaywall("10.1/x", "https://pub.example/x.pdf"))
    got = await oa.find_candidates({"DOI": "10.1/x", "URL": url})
    assert [s for s, _ in got].count("url") == 0


def test_a_url_alone_is_something_to_look_up():
    assert oa.has_identifier({"URL": "https://lab.example/paper.pdf"})
    assert not oa.has_identifier({"URL": "not a url"})


async def test_fetch_uses_the_reference_url(client, library, net):
    """No DOI, no arXiv id: a reference whose URL is a public PDF still gets it."""
    net["https://lab.example/hamilton2021.pdf"] = (200, PDF, {})
    item = await _add(client, library, URL="https://lab.example/hamilton2021.pdf")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 201
    assert r.json()["source"] == "url"
    assert r.json()["url"] == "https://lab.example/hamilton2021.pdf"


async def test_fetch_ignores_a_reference_url_that_is_not_a_pdf(client, library, net):
    net["https://lab.example/paper"] = (200, b"<html>abstract page</html>", {})
    item = await _add(client, library, URL="https://lab.example/paper")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 404
    assert "URL" in r.json()["detail"]


async def test_fetch_never_follows_a_reference_url_to_an_internal_host(client, library, net):
    net["http://internal.example/secret.pdf"] = (200, PDF, {})
    item = await _add(client, library, URL="http://internal.example/secret.pdf")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 404
    assert net.seen == []  # refused before any request was made
    assert not (await client.get(f"/items/{item['id']}")).json()["attachments"]


# ---------- odd reference URLs ----------


@pytest.mark.parametrize(
    "url", [["https://lab.example/x.pdf"], 42, "http://[::1", "http://a:x/", None]
)
async def test_odd_reference_urls_are_ignored_not_errors(net, url):
    assert not oa.has_identifier({"URL": url})
    assert await oa.find_candidates({"URL": url}) == []


async def test_fetch_with_a_malformed_url_is_a_404_not_a_500(client, library, net):
    item = await _add(client, library, URL="http://[::1")
    r = await client.post(f"/items/{item['id']}/fetch-pdf")
    assert r.status_code == 404


async def test_library_fetch_survives_malformed_urls(client, library, net):
    await _add(client, library, URL="http://[::1", title="Broken URL")
    await _add(client, library, URL=["not", "a", "string"], title="List URL")
    r = await client.post(f"/libraries/{library}/fetch-pdfs")
    assert r.status_code == 200
    assert r.json()["checked"] == 2 and r.json()["no_identifier"] == 2


async def test_a_redirect_to_a_malformed_url_is_a_miss(net):
    net["https://pub.example/a"] = (302, b"", {"location": "http://[::1"})
    assert await oa.download_pdf("https://pub.example/a") is None


async def test_old_style_arxiv_url_is_still_tried(net):
    """No arXiv candidate comes from hep-th/9901001, so the URL isn't "covered"."""
    csl = {"URL": "https://arxiv.org/pdf/hep-th/9901001"}
    assert oa.has_identifier(csl)
    assert await oa.find_candidates(csl) == [("url", "https://arxiv.org/pdf/hep-th/9901001")]


async def test_a_doi_org_url_without_a_doi_field_is_looked_up_by_its_doi(net):
    net.update(_unpaywall("10.1/x", "https://pub.example/x.pdf"))
    csl = {"URL": "https://doi.org/10.1/X"}
    assert oa.has_identifier(csl)
    assert await oa.find_candidates(csl) == [("unpaywall", "https://pub.example/x.pdf")]


async def test_reference_url_is_deduped_against_candidates_by_normalized_form(net):
    net.update(_unpaywall("10.1/x", "https://pub.example/x.pdf"))
    csl = {"DOI": "10.1/x", "URL": "HTTPS://Pub.Example:443/x.pdf#page=2"}
    assert [s for s, _ in await oa.find_candidates(csl)] == ["unpaywall"]
