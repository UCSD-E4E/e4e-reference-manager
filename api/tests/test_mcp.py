"""MCP server at /mcp: agents connect with a personal API token and get tools over the
reference library. Driven end-to-end with the MCP SDK's own client over an in-process
transport, so this exercises the real protocol, token check and REST permissions.
External services (translation-server, Crossref) are monkeypatched."""
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
import pytest_asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import app.validation as v
from app.auth import get_auth_context, new_api_token
from app.main import app
from app.mcp_server import mcp_endpoint
from app.models import Attachment, Item, Library

EXPECTED_TOOLS = {
    "list_libraries",
    "search_references",
    "list_references",
    "get_reference",
    "export_bibtex",
    "list_collections",
    "list_collection_references",
    "add_reference",
    "import_bibtex",
    "add_note",
    "create_collection",
    "add_to_collection",
    "validate_reference",
    "get_pdf_text",
    "get_pdf_url",
    "find_pdf",
    "find_library_pdfs",
    "update_reference",
    "delete_reference",
    "list_trash",
    "restore_reference",
    "create_pdf_upload",
}

ATTENTION = (
    "@article{vaswani2017,title={Attention Is All You Need},"
    "author={Vaswani, Ashish and Shazeer, Noam},journal={NeurIPS},"
    "year={2017},doi={10.1/attention}}"
)


@pytest.fixture
def mcp_app(client):
    """Real token auth: drop the test-user override."""
    app.dependency_overrides.pop(get_auth_context, None)


async def _token(db, user) -> str:
    raw, row = new_api_token(user, "agent", datetime.now(timezone.utc) + timedelta(days=1))
    db.add(row)
    await db.commit()
    return raw


@asynccontextmanager
async def _session(token: str):
    transport = httpx2.ASGITransport(app=app)
    async with mcp_endpoint.running(), httpx2.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as http:
        async with streamable_http_client("http://test/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                yield s


class Agent:
    """An MCP client holding a token. The server is stateless, so each call opens its own
    session; that keeps anyio task groups inside one task (pytest-asyncio tears down
    async fixtures in another, which anyio forbids)."""

    def __init__(self, token: str):
        self.token = token

    async def list_tools(self):
        async with _session(self.token) as s:
            return (await s.list_tools()).tools

    async def call_tool(self, tool: str, args: dict):
        async with _session(self.token) as s:
            return await s.call_tool(tool, args)


@pytest_asyncio.fixture
async def agent(mcp_app, db, alice):
    """An MCP client authenticated as alice."""
    return Agent(await _token(db, alice))


async def _call(agent: Agent, tool: str, **args):
    r = await agent.call_tool(tool, args)
    assert not r.is_error, r.content
    return json.loads(r.content[0].text)


async def _call_error(agent: Agent, tool: str, **args) -> str:
    r = await agent.call_tool(tool, args)
    assert r.is_error
    return r.content[0].text


@pytest.fixture
def registrars(monkeypatch):
    async def lookup(doi):
        return {"title": "Attention Is All You Need"} if doi == "10.1/attention" else None

    async def search(title, authors=None):
        return None

    monkeypatch.setattr(v, "crossref_lookup", lookup)
    monkeypatch.setattr(v, "crossref_search", search)
    v.clear_validation_cache()


# ── Transport & auth ────────────────────────────────────────────────────────────────────


async def test_lists_tools(agent):
    tools = await agent.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.description for t in tools)


@pytest.mark.parametrize("auth", [None, "Bearer refman_nope", "Basic eDp5"])
async def test_rejects_requests_without_a_valid_token(mcp_app, auth):
    headers = {"Accept": "application/json, text/event-stream"}
    if auth:
        headers["Authorization"] = auth
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        r = await http.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert r.status_code == 401
    assert r.headers["www-authenticate"].lower().startswith("bearer")


async def test_expired_token_is_rejected(mcp_app, db, alice):
    raw, row = new_api_token(alice, "old", datetime.now(timezone.utc) - timedelta(minutes=1))
    db.add(row)
    await db.commit()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        r = await http.post(
            "/mcp",
            headers={"Authorization": f"Bearer {raw}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert r.status_code == 401


# ── Tools ───────────────────────────────────────────────────────────────────────────────


async def _new_library(db, alice) -> str:
    lib = Library(name="Thesis", owner_id=alice.id)
    db.add(lib)
    await db.commit()
    return str(lib.id)


async def test_list_libraries(agent, db, alice):
    lib_id = await _new_library(db, alice)
    libs = await _call(agent, "list_libraries")
    assert libs == {
        "libraries": [{"id": lib_id, "name": "Thesis", "description": "", "my_access": "manage"}]
    }


async def test_import_list_get_and_export(agent, db, alice, registrars):
    lib_id = await _new_library(db, alice)
    result = await _call(agent, "import_bibtex", library_id=lib_id, bibtex=ATTENTION)
    assert result["imported"] == 1

    listed = await _call(agent, "list_references", library_id=lib_id)
    assert listed["total"] == 1
    [ref] = listed["references"]
    assert ref["title"] == "Attention Is All You Need"
    assert ref["authors"] == "Vaswani, Ashish; Shazeer, Noam"
    assert ref["year"] == 2017
    assert ref["doi"] == "10.1/attention"
    assert ref["citation_key"] == "vaswani2017"
    assert ref["validation"] == "verified"
    assert "csl_json" not in ref  # list views stay compact

    full = await _call(agent, "get_reference", reference_id=ref["id"])
    assert full["csl_json"]["title"] == "Attention Is All You Need"
    assert full["notes"] == []

    bib = await _call(agent, "export_bibtex", library_id=lib_id)
    assert "@article{vaswani2017" in bib["bibtex"]


async def test_search_references(agent, db, alice, registrars):
    lib_id = await _new_library(db, alice)
    await _call(agent, "import_bibtex", library_id=lib_id, bibtex=ATTENTION)
    hits = await _call(
        agent, "search_references", library_id=lib_id, query="attention", mode="keyword"
    )
    assert [h["citation_key"] for h in hits["references"]] == ["vaswani2017"]


async def test_add_reference_by_identifier(agent, db, alice, monkeypatch):
    async def fake_fetch(query):
        assert query == "10.1/attention"
        return [
            {
                "type": "article-journal",
                "title": "Attention Is All You Need",
                "DOI": "10.1/attention",
                "author": [{"family": "Vaswani", "given": "Ashish"}],
                "issued": {"date-parts": [[2017]]},
            }
        ]

    monkeypatch.setattr("app.routers.ingest.fetch_csl", fake_fetch)
    lib_id = await _new_library(db, alice)
    out = await _call(agent, "add_reference", library_id=lib_id, identifier="10.1/attention")
    assert [r["status"] for r in out["results"]] == ["created"]
    again = await _call(agent, "add_reference", library_id=lib_id, identifier="10.1/attention")
    assert [r["status"] for r in again["results"]] == ["duplicate"]


async def test_notes_and_collections(agent, db, alice, registrars):
    lib_id = await _new_library(db, alice)
    await _call(agent, "import_bibtex", library_id=lib_id, bibtex=ATTENTION)
    [ref] = (await _call(agent, "list_references", library_id=lib_id))["references"]

    note = await _call(agent, "add_note", reference_id=ref["id"], text="Key transformer paper")
    assert note["body"] == "Key transformer paper"
    full = await _call(agent, "get_reference", reference_id=ref["id"])
    assert [n["body"] for n in full["notes"]] == ["Key transformer paper"]

    coll = await _call(agent, "create_collection", library_id=lib_id, name="Background")
    assert (await _call(agent, "list_collections", library_id=lib_id)) == {
        "collections": [{"id": coll["id"], "name": "Background", "parent_id": None}]
    }
    await _call(agent, "add_to_collection", collection_id=coll["id"], reference_id=ref["id"])
    in_coll = await _call(agent, "list_collection_references", collection_id=coll["id"])
    assert [r["id"] for r in in_coll["references"]] == [ref["id"]]
    bib = await _call(agent, "export_bibtex", collection_id=coll["id"])
    assert "vaswani2017" in bib["bibtex"]


async def test_validate_reference(agent, db, alice, registrars):
    lib_id = await _new_library(db, alice)
    await _call(agent, "import_bibtex", library_id=lib_id, bibtex=ATTENTION)
    [ref] = (await _call(agent, "list_references", library_id=lib_id))["references"]
    verdict = await _call(agent, "validate_reference", reference_id=ref["id"])
    assert verdict["status"] == "verified"


async def test_export_needs_exactly_one_target(agent):
    assert "exactly one" in await _call_error(agent, "export_bibtex")


async def test_tools_respect_library_permissions(agent, db, make_user):
    """A tool can only reach what the token's owner can: errors come back as tool errors."""
    bob = await make_user("bob@e4e.local", "Bob")
    bobs = Library(name="Bob's", owner_id=bob.id)
    db.add(bobs)
    await db.commit()
    assert await _call(agent, "list_libraries") == {"libraries": []}
    err = await _call_error(agent, "list_references", library_id=str(bobs.id))
    assert "403" in err or "404" in err


# ── Robustness ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": -1}, {"offset": -5}, {"limit": 201}])
async def test_paging_arguments_are_validated(agent, db, alice, args):
    lib_id = await _new_library(db, alice)
    err = await _call_error(agent, "list_references", library_id=lib_id, **args)
    assert "limit" in err or "offset" in err


async def test_inner_crash_is_a_clean_tool_error(agent, db, alice, monkeypatch):
    async def boom(query):
        raise RuntimeError("translation-server exploded")

    monkeypatch.setattr("app.routers.ingest.fetch_csl", boom)
    lib_id = await _new_library(db, alice)
    err = await _call_error(agent, "add_reference", library_id=lib_id, identifier="10.1/x")
    assert "500: Internal Server Error" in err


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"detail": "Library not found"}, "404: Library not found"),
        ({"detail": [{"msg": "bad"}]}, "404: [{'msg': 'bad'}]"),
        (["not", "an", "object"], "404: ['not', 'an', 'object']"),
        ("plain", "404: plain"),
    ],
)
def test_error_detail_handles_any_body(body, expected):
    import httpx

    from app.mcp_server import _error_message

    r = httpx.Response(404, text=body) if isinstance(body, str) else httpx.Response(404, json=body)
    assert _error_message(r) == expected


@pytest.mark.parametrize("auth", ["Bearer ", "Bearer    ", "Bearer\t"])
async def test_empty_bearer_token_gets_a_clear_message(mcp_app, auth):
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        r = await http.post(
            "/mcp",
            headers={"Authorization": auth},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert r.status_code == 401
    assert "required" in r.json()["detail"]


async def test_not_running_is_503(mcp_app, db, alice):
    """Outside the app's lifespan there is no session manager: say so, don't 500."""
    token = await _token(db, alice)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        r = await http.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert r.status_code == 503


async def test_tool_annotations_are_honest(agent):
    tools = {t.name: t.annotations for t in await agent.list_tools()}
    read_only = {n for n, a in tools.items() if a.read_only_hint}
    assert read_only == {
        "list_libraries",
        "search_references",
        "list_references",
        "get_reference",
        "export_bibtex",
        "list_collections",
        "list_collection_references",
        "get_pdf_text",
        "get_pdf_url",
        "list_trash",
    }
    # These overwrite stored data (metadata; the validation verdict): updates, not adds.
    updates = {"update_reference", "validate_reference", "delete_reference"}
    assert {n for n, a in tools.items() if a.destructive_hint} == updates


# ── PDFs ────────────────────────────────────────────────────────────────────────────────


async def _paper_with_pdf(db, alice, text="Stereo cameras measure fish length underwater."):
    lib = Library(name="FishSense", owner_id=alice.id)
    db.add(lib)
    await db.flush()
    item = Item(
        library_id=lib.id,
        citation_key="fish2024",
        type="article-journal",
        csl_json={"type": "article-journal", "title": "Measuring Fish", "DOI": "10.1/fish"},
        title="Measuring Fish",
        doi="10.1/fish",
    )
    db.add(item)
    await db.flush()
    att = Attachment(
        item_id=item.id,
        filename="fish2024.pdf",
        content_type="application/pdf",
        size=1234,
        sha256="0" * 64,
        storage_key=f"{lib.id}/{item.id}/abc",
        text=text,
    )
    db.add(att)
    await db.commit()
    return str(lib.id), str(item.id), str(att.id)


async def test_get_reference_lists_pdfs(agent, db, alice):
    lib_id, item_id, att_id = await _paper_with_pdf(db, alice)
    ref = await _call(agent, "get_reference", reference_id=item_id)
    assert ref["attachments"] == [
        {"id": att_id, "filename": "fish2024.pdf", "content_type": "application/pdf", "size": 1234}
    ]
    [summary] = (await _call(agent, "list_references", library_id=lib_id))["references"]
    assert summary["has_pdf"] is True


async def test_get_pdf_text_pages(agent, db, alice):
    _, _, att_id = await _paper_with_pdf(db, alice, text="x" * 25)
    first = await _call(agent, "get_pdf_text", attachment_id=att_id, max_chars=10)
    assert first == {"text": "x" * 10, "offset": 0, "total_chars": 25, "next_offset": 10}
    last = await _call(agent, "get_pdf_text", attachment_id=att_id, offset=20, max_chars=10)
    assert last["text"] == "x" * 5
    assert last["next_offset"] is None


async def test_get_pdf_url(agent, db, alice, monkeypatch):
    monkeypatch.setattr(
        "app.routers.attachments.presigned_get_url",
        lambda key, name=None: f"https://s3.example/{key}?sig",
    )
    _, _, att_id = await _paper_with_pdf(db, alice)
    out = await _call(agent, "get_pdf_url", attachment_id=att_id)
    assert out["url"].startswith("https://s3.example/")
    assert out["content_type"] == "application/pdf"
    assert out["expires_in_seconds"] > 0


async def test_find_pdf(agent, db, alice, monkeypatch):
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Open access fish paper")
    pdf = doc.tobytes()

    async def found(csl):
        return pdf, "unpaywall", "https://oa.example/fish.pdf"

    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    monkeypatch.setattr("app.oa_pdf.fetch_open_access_pdf", found)
    lib = Library(name="L", owner_id=alice.id)
    db.add(lib)
    await db.flush()
    item = Item(library_id=lib.id, citation_key="k", type="article-journal",
                csl_json={"DOI": "10.1/oa"}, title="OA", doi="10.1/oa")
    db.add(item)
    await db.commit()

    out = await _call(agent, "find_pdf", reference_id=str(item.id))
    assert out["source"] == "unpaywall"
    assert out["attachment"]["filename"].endswith(".pdf")
    text = await _call(agent, "get_pdf_text", attachment_id=out["attachment"]["id"])
    assert "Open access fish paper" in text["text"]


async def test_find_pdf_not_found_is_a_tool_error(agent, db, alice, monkeypatch):
    async def nothing(csl):
        return None

    monkeypatch.setattr("app.oa_pdf.fetch_open_access_pdf", nothing)
    _, item_id, _ = await _paper_with_pdf(db, alice)
    err = await _call_error(agent, "find_pdf", reference_id=item_id)
    assert "No open-access PDF" in err


async def test_find_library_pdfs(agent, db, alice, monkeypatch):
    async def nothing(csl):
        return None

    monkeypatch.setattr("app.oa_pdf.fetch_open_access_pdf", nothing)
    lib_id, _, _ = await _paper_with_pdf(db, alice)
    out = await _call(agent, "find_library_pdfs", library_id=lib_id)
    assert out["checked"] == 1
    assert out["already_had_pdf"] == 1
    assert out["next_after"] is None


# ── Editing ─────────────────────────────────────────────────────────────────────────────


async def _imported(agent, db, alice) -> tuple[str, dict]:
    lib_id = await _new_library(db, alice)
    await _call(agent, "import_bibtex", library_id=lib_id, bibtex=ATTENTION)
    [ref] = (await _call(agent, "list_references", library_id=lib_id))["references"]
    return lib_id, await _call(agent, "get_reference", reference_id=ref["id"])


async def test_update_reference_merges_fields_and_revalidates(agent, db, alice, registrars):
    _, ref = await _imported(agent, db, alice)
    assert ref["version"] == 1

    out = await _call(
        agent,
        "update_reference",
        reference_id=ref["id"],
        version=ref["version"],
        changes={"title": "A Paper That Does Not Exist", "volume": "30", "journal": None},
    )
    assert out["version"] == 2
    assert out["title"] == "A Paper That Does Not Exist"
    assert out["validation"] == "metadata_mismatch"  # re-checked: DOI is Attention's

    full = await _call(agent, "get_reference", reference_id=ref["id"])
    assert full["csl_json"]["volume"] == "30"
    assert full["csl_json"]["container-title"] == "NeurIPS"  # untouched fields kept
    assert full["authors"] == "Vaswani, Ashish; Shazeer, Noam"


async def test_update_reference_null_removes_a_field(agent, db, alice, registrars):
    _, ref = await _imported(agent, db, alice)
    out = await _call(
        agent, "update_reference", reference_id=ref["id"], version=1, changes={"DOI": None}
    )
    assert out["doi"] is None
    full = await _call(agent, "get_reference", reference_id=ref["id"])
    assert "DOI" not in full["csl_json"]


async def test_update_reference_rejects_stale_version(agent, db, alice, registrars):
    _, ref = await _imported(agent, db, alice)
    await _call(
        agent, "update_reference", reference_id=ref["id"], version=1, changes={"volume": "1"}
    )
    err = await _call_error(
        agent, "update_reference", reference_id=ref["id"], version=1, changes={"volume": "2"}
    )
    assert "409" in err
    assert "get_reference" in err  # tells the agent how to recover


async def test_update_reference_can_change_citation_key(agent, db, alice, registrars):
    _, ref = await _imported(agent, db, alice)
    out = await _call(
        agent, "update_reference", reference_id=ref["id"], version=1, citation_key="vaswani2017attn"
    )
    assert out["citation_key"] == "vaswani2017attn"


async def test_update_reference_needs_some_change(agent, db, alice, registrars):
    _, ref = await _imported(agent, db, alice)
    err = await _call_error(agent, "update_reference", reference_id=ref["id"], version=1)
    assert "Nothing to change" in err


async def test_update_survives_a_failed_revalidation(agent, db, alice, registrars, monkeypatch):
    """The edit is saved even if the follow-up check can't run; the agent is told."""
    _, ref = await _imported(agent, db, alice)

    async def broken(csl):
        raise RuntimeError("crossref down")

    monkeypatch.setattr("app.validation.validate_item_csl", broken)
    out = await _call(
        agent, "update_reference", reference_id=ref["id"], version=1, changes={"volume": "9"}
    )
    assert out["version"] == 2
    assert out["validation"] == "error"
    assert "500" in out["validation_error"]


async def test_update_is_recorded_in_history(agent, db, alice, registrars):
    from sqlalchemy import select

    from app.models import AuditEvent

    _, ref = await _imported(agent, db, alice)
    await _call(
        agent, "update_reference", reference_id=ref["id"], version=1, changes={"volume": "3"}
    )
    ops = (
        await db.execute(
            select(AuditEvent.operation).where(AuditEvent.entity_id == uuid.UUID(ref["id"]))
        )
    ).scalars().all()
    assert "update" in ops


# ── Deleting (to the trash) ─────────────────────────────────────────────────────────────


async def test_delete_reference_moves_it_to_the_trash(agent, db, alice, registrars):
    lib_id, ref = await _imported(agent, db, alice)
    out = await _call(agent, "delete_reference", reference_id=ref["id"])
    assert out["reference_id"] == ref["id"]
    restorable = datetime.fromisoformat(out["restorable_until"])
    assert abs(restorable - (datetime.now(timezone.utc) + timedelta(days=30))) < timedelta(
        minutes=1
    )
    assert (await _call(agent, "list_references", library_id=lib_id))["total"] == 0
    assert "404" in await _call_error(agent, "get_reference", reference_id=ref["id"])

    trash = await _call(agent, "list_trash", library_id=lib_id)
    [entry] = trash["trash"]
    assert entry["id"] == ref["id"]
    assert entry["title"] == "Attention Is All You Need"
    assert entry["deleted_by"] == "alice@e4e.local"


async def test_restore_reference_undoes_a_delete(agent, db, alice, registrars):
    lib_id, ref = await _imported(agent, db, alice)
    await _call(agent, "add_note", reference_id=ref["id"], text="keep me")
    await _call(agent, "delete_reference", reference_id=ref["id"])

    back = await _call(agent, "restore_reference", library_id=lib_id, reference_id=ref["id"])
    assert back["id"] == ref["id"]
    full = await _call(agent, "get_reference", reference_id=ref["id"])
    assert [n["body"] for n in full["notes"]] == ["keep me"]
    assert (await _call(agent, "list_trash", library_id=lib_id)) == {"trash": []}


async def test_delete_reference_respects_permissions(agent, db, make_user):
    bob = await make_user("bob@e4e.local", "Bob")
    lib = Library(name="Bob's", owner_id=bob.id)
    db.add(lib)
    await db.flush()
    item = Item(library_id=lib.id, citation_key="k", type="document", csl_json={}, title="B")
    db.add(item)
    await db.commit()
    err = await _call_error(agent, "delete_reference", reference_id=str(item.id))
    assert "404" in err or "403" in err
    await db.refresh(item)
    assert item.deleted_at is None


async def test_create_pdf_upload_then_curl_it(agent, db, alice, monkeypatch, client):
    """The agent gets a one-time link and a curl command; the file is POSTed to the link
    directly (here through the test client), never through the tool call."""
    import fitz

    monkeypatch.setattr("app.routers.attachments.upload_bytes", lambda *a, **k: None)
    lib_id, item_id, _ = await _paper_with_pdf(db, alice)
    out = await _call(agent, "create_pdf_upload", reference_id=item_id)
    assert out["curl"].startswith("curl -fsS -F file=@paper.pdf '")
    assert out["url"] in out["curl"]
    assert "expires_at" in out

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Downloaded paywalled copy")
    path = out["url"].split("://", 1)[1].split("/", 1)[1]
    r = await client.post(f"/{path}", files={"file": ("p.pdf", doc.tobytes(), "application/pdf")})
    assert r.status_code == 201
    ref = await _call(agent, "get_reference", reference_id=item_id)
    assert len(ref["attachments"]) == 2
