"""Hostile or oversized input must get a clear 4xx, never a 500 or an unbounded read:
upload size caps, .bib parsing off the event loop, out-of-range years and NUL bytes in
imports, and attachments that could be served as a web page."""
from urllib.parse import parse_qs, urlsplit

import pytest

import app.upload_limits as limits

PDF = b"%PDF-1.4 tiny"


async def _add(client, library, title="A paper"):
    r = await client.post(
        f"/libraries/{library}/items", json={"csl_json": {"type": "article", "title": title}}
    )
    return r.json()


@pytest.fixture
def no_storage(monkeypatch):
    stored = {}

    def fake_upload(key, data, content_type):
        stored[key] = content_type

    monkeypatch.setattr("app.routers.attachments.upload_bytes", fake_upload)
    monkeypatch.setattr("app.routers.ingest.upload_bytes", fake_upload)
    return stored


# ---------- size caps ----------


async def test_attachment_upload_is_capped(client, library, no_storage, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_BYTES", 100)
    item = await _add(client, library)
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", PDF + b"x" * 200, "application/pdf")},
    )
    assert r.status_code == 413
    assert "MB" in r.json()["detail"]
    assert no_storage == {}


async def test_from_pdf_upload_is_capped(client, library, no_storage, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_BYTES", 100)
    r = await client.post(
        f"/libraries/{library}/items/from-pdf",
        files={"file": ("p.pdf", PDF + b"x" * 200, "application/pdf")},
    )
    assert r.status_code == 413
    assert (await client.get(f"/libraries/{library}/items")).json()["total"] == 0


async def test_pdf_validate_upload_is_capped(client, library, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_BYTES", 100)
    r = await client.post(
        "/pdf-validate", files={"file": ("p.pdf", PDF + b"x" * 200, "application/pdf")}
    )
    assert r.status_code == 413


async def test_bib_import_is_capped(client, library, monkeypatch):
    monkeypatch.setattr(limits, "MAX_BIB_BYTES", 100)
    bib = b"@misc{k, title={T}}\n" + b"%" * 200
    r = await client.post(f"/libraries/{library}/import", files={"file": ("a.bib", bib)})
    assert r.status_code == 413


async def test_upload_link_uses_the_shared_cap(client, library, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_BYTES", 100)
    item = await _add(client, library)
    link = (await client.post(f"/items/{item['id']}/upload-links")).json()
    path = urlsplit(link["url"]).path
    r = await client.post(path, files={"file": ("p.pdf", PDF + b"x" * 200, "application/pdf")})
    assert r.status_code == 413


# ---------- .bib import: parsing off the loop, bad years, NUL bytes ----------


async def test_bib_parsing_runs_in_a_worker_thread(client, library, monkeypatch):
    import app.routers.bib as bib_router

    calls = []
    real = bib_router.run_in_threadpool

    async def spy(fn, *a, **k):
        calls.append(fn.__name__)
        return await real(fn, *a, **k)

    monkeypatch.setattr(bib_router, "run_in_threadpool", spy)
    bib = b"@misc{k, title={T}, year={2020}}"
    r = await client.post(f"/libraries/{library}/import", files={"file": ("a.bib", bib)})
    assert r.status_code == 200
    assert "parse_bibtex" in calls


async def test_bib_import_survives_absurd_years_and_nul_bytes(client, library):
    bib = (
        b"@misc{huge, title={Huge year}, year={99999999999}}\n"
        b"@misc{neg, title={Negative year}, year={-5}}\n"
        b"@misc{nul, title={Nul\x00 byte}, author={Ng\x00, A}, year={2020}}\n"
    )
    r = await client.post(f"/libraries/{library}/import", files={"file": ("a.bib", bib)})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3
    items = (await client.get(f"/libraries/{library}/items")).json()["items"]
    by_title = {it["title"]: it for it in items}
    assert by_title["Huge year"]["year"] is None
    assert by_title["Negative year"]["year"] is None
    assert by_title["Nul byte"]["year"] == 2020


async def test_item_with_an_absurd_year_does_not_500(client, library):
    csl = {"type": "article", "title": "Far future", "issued": {"date-parts": [[10**12]]}}
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    assert r.json()["year"] is None


# ---------- attachments can't be served as web pages ----------


async def test_uploaded_pdf_with_a_generic_type_is_stored_as_pdf(client, library, no_storage):
    item = await _add(client, library)
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("p.pdf", PDF, "application/octet-stream")},
    )
    assert r.status_code == 201
    assert r.json()["content_type"] == "application/pdf"
    assert list(no_storage.values()) == ["application/pdf"]


async def test_non_pdf_upload_is_never_stored_as_html(client, library, no_storage):
    item = await _add(client, library)
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("x.html", b"<script>alert(1)</script>", "text/html")},
    )
    assert r.status_code == 201
    # object storage serves it as an opaque download, whatever the client claimed
    assert list(no_storage.values()) == ["application/octet-stream"]


async def test_a_fake_pdf_claim_is_not_trusted(client, library, no_storage):
    item = await _add(client, library)
    r = await client.post(
        f"/items/{item['id']}/attachments",
        files={"file": ("x.pdf", b"<html>gotcha</html>", "application/pdf")},
    )
    assert r.json()["content_type"] != "application/pdf"


def _query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_inline_urls_force_the_pdf_content_type():
    from app.storage import presigned_get_url

    q = _query(presigned_get_url("lib/item/sha"))
    assert q["response-content-type"] == "application/pdf"
    assert "response-content-disposition" not in q


def test_download_urls_are_opaque_attachments_with_a_safe_filename():
    from app.storage import presigned_get_url

    q = _query(presigned_get_url("lib/item/sha", 'evil"\r\nX-Injected: 1.html'))
    assert q["response-content-type"] == "application/octet-stream"
    disposition = q["response-content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2


async def test_viewer_url_for_a_non_pdf_is_a_download(client, library, no_storage, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.routers.attachments.presigned_get_url",
        lambda key, name=None: calls.append(name) or "https://s3/x",
    )
    item = await _add(client, library)
    att = (
        await client.post(
            f"/items/{item['id']}/attachments",
            files={"file": ("notes.txt", b"plain text", "text/plain")},
        )
    ).json()
    assert (await client.get(f"/attachments/{att['id']}/url")).status_code == 200
    assert calls == ["notes.txt"]  # presigned as a download, never inline
