"""MCP server: lets AI agents (Claude Code, Claude Desktop, ...) work with the library.

Served at /mcp on the API (public URL: https://bib.krg.ucsd.edu/api/mcp) over streamable
HTTP, stateless, JSON responses. Clients authenticate with a personal API token
(`Authorization: Bearer refman_...`, see auth.py).

Each tool is a thin wrapper over the REST API, called in-process with the caller's own
Authorization header, so tools get exactly the REST endpoints' permissions, validation
and audit trail, and no logic is duplicated here. Tools add, but never delete.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

mcp = MCPServer(
    name="e4e Reference Manager",
    instructions=(
        "Tools for the e4e lab's shared reference library. Start with list_libraries to "
        "get library ids. References have a stable `id` (use it with other tools) and a "
        "BibTeX `citation_key`. Prefer search_references for finding papers. Before "
        "citing a reference you did not import yourself, check its `validation` status "
        "(verified / metadata_mismatch / not_found / unverifiable); validate_reference "
        "re-checks it against Crossref/arXiv."
    ),
)

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_ADD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
_ADD_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
# Tools that reach outside services (translation-server, Crossref, arXiv).
_ADD_EXTERNAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_CHECK_EXTERNAL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)


# ── Calling the REST API as the caller ──────────────────────────────────────────────────

_api_app: ASGIApp | None = None


@asynccontextmanager
async def _api(authorization: str) -> AsyncIterator[httpx.AsyncClient]:
    assert _api_app is not None, "MCP endpoint not mounted"
    transport = httpx.ASGITransport(app=_api_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://refman.internal",
        headers={"Authorization": authorization},
        timeout=120,
    ) as client:
        yield client


async def _request(ctx: Context, method: str, path: str, **kwargs: Any) -> httpx.Response:
    authorization = (ctx.headers or {}).get("authorization", "")
    async with _api(authorization) as api:
        r = await api.request(method, path, **kwargs)
    if r.is_error:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"{r.status_code}: {detail}")
    return r


async def _json(ctx: Context, method: str, path: str, **kwargs: Any) -> Any:
    return (await _request(ctx, method, path, **kwargs)).json()


# ── Shaping results for a model: compact by default ─────────────────────────────────────


def _authors(csl: dict) -> str:
    names = []
    for a in csl.get("author") or []:
        if a.get("literal"):
            names.append(a["literal"])
        else:
            names.append(", ".join(p for p in (a.get("family"), a.get("given")) if p))
    return "; ".join(n for n in names if n)


def _summary(item: dict) -> dict:
    csl = item.get("csl_json") or {}
    return {
        "id": item["id"],
        "citation_key": item["citation_key"],
        "type": item["type"],
        "title": item["title"],
        "authors": _authors(csl),
        "year": item["year"],
        "doi": item["doi"],
        "container": csl.get("container-title"),
        "validation": (item.get("validation") or {}).get("status"),
    }


def _item_list(data: dict) -> dict:
    return {"total": data["total"], "references": [_summary(i) for i in data["items"]]}


# ── Tools: reading ──────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_READ)
async def list_libraries(ctx: Context) -> dict:
    """List the libraries (projects) you can access, with your access level
    (view / edit / manage) on each."""
    libs = await _json(ctx, "GET", "/libraries")
    return {
        "libraries": [
            {k: lib[k] for k in ("id", "name", "description", "my_access")} for lib in libs
        ]
    }


@mcp.tool(annotations=_READ)
async def search_references(
    ctx: Context,
    library_id: uuid.UUID,
    query: str,
    mode: Literal["hybrid", "keyword", "semantic"] = "hybrid",
    limit: int = 20,
) -> dict:
    """Search a library. `hybrid` (default) fuses full-text keyword ranking with semantic
    similarity; `keyword` matches title/abstract/authors; `semantic` finds related work
    even without shared words. Results are ranked best-first."""
    data = await _json(
        ctx,
        "GET",
        f"/libraries/{library_id}/search",
        params={"q": query, "mode": mode, "limit": min(limit, 200)},
    )
    return _item_list(data)


@mcp.tool(annotations=_READ)
async def list_references(
    ctx: Context, library_id: uuid.UUID, limit: int = 50, offset: int = 0
) -> dict:
    """List a library's references, newest first. Page with `offset`; `total` is the
    library's size."""
    data = await _json(
        ctx,
        "GET",
        f"/libraries/{library_id}/items",
        params={"limit": min(limit, 200), "offset": offset},
    )
    return _item_list(data)


@mcp.tool(annotations=_READ)
async def get_reference(ctx: Context, reference_id: uuid.UUID) -> dict:
    """Get one reference in full: its CSL-JSON metadata (abstract, venue, pages, ...),
    validation verdict and the notes people have left on it."""
    item = await _json(ctx, "GET", f"/items/{reference_id}")
    notes = await _json(ctx, "GET", f"/items/{reference_id}/notes")
    return {
        **_summary(item),
        "library_id": item["library_id"],
        "csl_json": item["csl_json"],
        "validation_details": item.get("validation"),
        "notes": [{"id": n["id"], "body": n["body"], "created_at": n["created_at"]} for n in notes],
    }


@mcp.tool(annotations=_READ)
async def export_bibtex(
    ctx: Context,
    library_id: uuid.UUID | None = None,
    collection_id: uuid.UUID | None = None,
) -> dict:
    """Export BibTeX for a whole library or one collection: pass exactly one of
    `library_id` or `collection_id`."""
    if (library_id is None) == (collection_id is None):
        raise ToolError("Pass exactly one of library_id or collection_id")
    path = (
        f"/libraries/{library_id}/export.bib"
        if library_id
        else f"/collections/{collection_id}/export.bib"
    )
    return {"bibtex": (await _request(ctx, "GET", path)).text}


@mcp.tool(annotations=_READ)
async def list_collections(ctx: Context, library_id: uuid.UUID) -> dict:
    """List a library's collections (folders). `parent_id` links nested collections."""
    colls = await _json(ctx, "GET", f"/libraries/{library_id}/collections")
    return {"collections": [{k: c[k] for k in ("id", "name", "parent_id")} for c in colls]}


@mcp.tool(annotations=_READ)
async def list_collection_references(ctx: Context, collection_id: uuid.UUID) -> dict:
    """List the references filed in a collection."""
    items = await _json(ctx, "GET", f"/collections/{collection_id}/items")
    return _item_list({"total": len(items), "items": items})


# ── Tools: adding (never deleting) ──────────────────────────────────────────────────────


@mcp.tool(annotations=_ADD_EXTERNAL)
async def add_reference(ctx: Context, library_id: uuid.UUID, identifier: str) -> dict:
    """Add a reference by DOI, arXiv id, PMID, ISBN or URL; its metadata is fetched
    automatically. A paper already in the library (same DOI) is reported as a
    duplicate, not added twice."""
    return await _json(
        ctx, "POST", f"/libraries/{library_id}/ingest", json={"query": identifier}
    )


@mcp.tool(annotations=_ADD_EXTERNAL)
async def import_bibtex(ctx: Context, library_id: uuid.UUID, bibtex: str) -> dict:
    """Add one or more BibTeX entries to a library. Duplicates (same DOI or title) are
    skipped and listed; each added entry is checked against Crossref/arXiv."""
    return await _json(
        ctx, "POST", f"/libraries/{library_id}/import/text", json={"bibtex": bibtex}
    )


@mcp.tool(annotations=_ADD)
async def add_note(ctx: Context, reference_id: uuid.UUID, text: str) -> dict:
    """Leave a note on a reference (visible to everyone with access to the library)."""
    note = await _json(ctx, "POST", f"/items/{reference_id}/notes", json={"body": text})
    return {k: note[k] for k in ("id", "body", "created_at")}


@mcp.tool(annotations=_ADD)
async def create_collection(
    ctx: Context, library_id: uuid.UUID, name: str, parent_id: uuid.UUID | None = None
) -> dict:
    """Create a collection (folder) in a library, optionally nested under `parent_id`."""
    body = {"name": name, "parent_id": str(parent_id) if parent_id else None}
    coll = await _json(ctx, "POST", f"/libraries/{library_id}/collections", json=body)
    return {k: coll[k] for k in ("id", "name", "parent_id")}


@mcp.tool(annotations=_ADD_IDEMPOTENT)
async def add_to_collection(
    ctx: Context, collection_id: uuid.UUID, reference_id: uuid.UUID
) -> dict:
    """File a reference in a collection of the same library."""
    await _request(ctx, "POST", f"/collections/{collection_id}/items/{reference_id}")
    return {"collection_id": str(collection_id), "reference_id": str(reference_id)}


@mcp.tool(annotations=_CHECK_EXTERNAL)
async def validate_reference(ctx: Context, reference_id: uuid.UUID) -> dict:
    """Check a reference against Crossref/arXiv to confirm the paper exists and its
    metadata matches (guards against hallucinated citations). Stores and returns the
    verdict: verified, metadata_mismatch, not_found or unverifiable."""
    return await _json(ctx, "POST", f"/items/{reference_id}/validate")


# ── HTTP endpoint ───────────────────────────────────────────────────────────────────────


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
    )


class McpEndpoint:
    """ASGI endpoint for /mcp: requires a valid API token, then hands the request to the
    SDK's session manager. A session manager can only run once, so `running()` builds a
    fresh one each time (once per app lifespan; once per test)."""

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
            # DNS-rebinding protection guards unauthenticated localhost servers; this one
            # is public and every request must carry a Bearer token.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with mcp.session_manager.run():
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        authorization = headers.get("authorization", "")
        # Demand a Bearer token outright: with REFMAN_DEV_AUTH the API would otherwise
        # accept a header-less request as the dev user.
        if not authorization.lower().startswith("bearer "):
            await _unauthorized("An API token is required (Authorization: Bearer ...)")(
                scope, receive, send
            )
            return
        async with _api(authorization) as api:
            if (await api.get("/auth/me")).status_code != 200:
                await _unauthorized("Invalid or expired API token")(scope, receive, send)
                return
        await mcp.session_manager.handle_request(scope, receive, send)


mcp_endpoint = McpEndpoint()


def mount_mcp(app: Any) -> None:
    """Serve the MCP endpoint at /mcp on `app` (the FastAPI API)."""
    global _api_app
    _api_app = app
    app.router.routes.append(
        Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"])
    )
