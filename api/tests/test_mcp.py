"""MCP server at /mcp: agents connect with a personal API token and get tools over the
reference library. Driven end-to-end with the MCP SDK's own client over an in-process
transport, so this exercises the real protocol, token check and REST permissions.
External services (translation-server, Crossref) are monkeypatched."""
import json
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
from app.models import Library

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
