"""MCP server: lets AI agents (Claude Code, Claude Desktop, ...) work with the library.

Served at /mcp on the API (public URL: https://bib.krg.ucsd.edu/api/mcp) over streamable
HTTP, stateless, JSON responses. Clients authenticate with a personal API token
(`Authorization: Bearer refman_...`, see auth.py).

Each tool is a thin wrapper over the REST API, called in-process with the caller's own
Authorization header, so tools get exactly the REST endpoints' permissions, validation
and audit trail, and no logic is duplicated here. Tools add and organise; none deletes
anything or edits a reference's metadata. The one update is validate_reference, which
refreshes a reference's stored validation verdict.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from .auth import authenticate_token
from .db import get_session

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
# Overwrites a stored verdict (e.g. a Crossref outage can turn "verified" into
# "unverifiable"), so clients should treat it as an update.
_CHECK_EXTERNAL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)

Limit = Annotated[int, Field(ge=1, le=200, description="Results per page (1-200)")]
Offset = Annotated[int, Field(ge=0, description="Results to skip")]


# ── Calling the REST API as the caller ──────────────────────────────────────────────────

_api_app: Any = None  # the FastAPI app, set by mount_mcp()


@asynccontextmanager
async def _api(authorization: str) -> AsyncIterator[httpx.AsyncClient]:
    assert _api_app is not None, "MCP endpoint not mounted"
    # raise_app_exceptions=False: a crash inside an endpoint comes back as its 500
    # response (reported to the agent as a tool error) rather than escaping here.
    transport = httpx.ASGITransport(app=_api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://refman.internal",
        headers={"Authorization": authorization},
    ) as client:
        yield client


def _caller_api(ctx: Context):
    """A client for the REST API that acts as the MCP caller (their token)."""
    return _api((ctx.headers or {}).get("authorization", ""))


def _error_message(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        body = r.text
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return f"{r.status_code}: {detail}"


def _checked(r: httpx.Response) -> httpx.Response:
    if r.is_error:
        raise ToolError(_error_message(r))
    return r


async def _request(ctx: Context, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with _caller_api(ctx) as api:
        return _checked(await api.request(method, path, **kwargs))


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
    limit: Limit = 20,
) -> dict:
    """Search a library. `hybrid` (default) fuses full-text keyword ranking with semantic
    similarity; `keyword` matches title/abstract/authors; `semantic` finds related work
    even without shared words. Results are ranked best-first."""
    data = await _json(
        ctx,
        "GET",
        f"/libraries/{library_id}/search",
        params={"q": query, "mode": mode, "limit": limit},
    )
    return _item_list(data)


@mcp.tool(annotations=_READ)
async def list_references(
    ctx: Context, library_id: uuid.UUID, limit: Limit = 50, offset: Offset = 0
) -> dict:
    """List a library's references, newest first. Page with `offset`; `total` is the
    library's size."""
    data = await _json(
        ctx,
        "GET",
        f"/libraries/{library_id}/items",
        params={"limit": limit, "offset": offset},
    )
    return _item_list(data)


@mcp.tool(annotations=_READ)
async def get_reference(ctx: Context, reference_id: uuid.UUID) -> dict:
    """Get one reference in full: its CSL-JSON metadata (abstract, venue, pages, ...),
    validation verdict and the notes people have left on it."""
    async with _caller_api(ctx) as api:
        item_r, notes_r = await asyncio.gather(
            api.get(f"/items/{reference_id}"), api.get(f"/items/{reference_id}/notes")
        )
    item, notes = _checked(item_r).json(), _checked(notes_r).json()
    return {
        **_summary(item),
        "library_id": item["library_id"],
        "csl_json": item["csl_json"],
        "validation_details": item.get("validation"),
        "notes": [
            {"id": n["id"], "body": n["body"], "created_at": n["created_at"]} for n in notes
        ],
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


# ── Tools: adding and organising ────────────────────────────────────────────────────────


@mcp.tool(annotations=_ADD_EXTERNAL)
async def add_reference(ctx: Context, library_id: uuid.UUID, identifier: str) -> dict:
    """Add a reference by DOI, arXiv id, PMID, ISBN or URL; its metadata is fetched
    automatically. A paper already in the library (same DOI, or same title and year) is
    reported as a duplicate, not added twice, so retrying is safe."""
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
    metadata matches (guards against hallucinated citations). Returns the verdict
    (verified, metadata_mismatch, not_found or unverifiable) and stores it on the
    reference, replacing the previous one. Needs edit access."""
    return await _json(ctx, "POST", f"/items/{reference_id}/validate")


# ── HTTP endpoint ───────────────────────────────────────────────────────────────────────


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
    )


def _bearer_token(authorization: str) -> str | None:
    """The token from `Bearer <token>`, parsed like the REST API's HTTPBearer; None if the
    header is missing, uses another scheme, or carries no token."""
    scheme, _, token = authorization.partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


async def _token_is_valid(token: str) -> bool:
    # Open the DB session the way the app does, honouring dependency overrides (tests).
    get_db = _api_app.dependency_overrides.get(get_session, get_session)
    async with aclosing(get_db()) as sessions:
        async for session in sessions:
            try:
                await authenticate_token(session, token)
            except HTTPException:
                return False
            return True
    return False


class McpEndpoint:
    """ASGI endpoint for /mcp: requires a valid API token, then hands the request to the
    SDK's session manager. A session manager can only run once, so `running()` builds a
    fresh one each time (once per app lifespan; once per test)."""

    def __init__(self) -> None:
        self._manager: Any = None

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
            # DNS-rebinding protection guards unauthenticated localhost servers; this one
            # is public and every request must carry a Bearer token.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        manager = mcp.session_manager
        async with manager.run():
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        # Demand a Bearer token outright: with REFMAN_DEV_AUTH the API would otherwise
        # accept a header-less request as the dev user.
        token = _bearer_token(headers.get("authorization", ""))
        if token is None:
            response = _unauthorized("An API token is required (Authorization: Bearer ...)")
        elif not await _token_is_valid(token):
            response = _unauthorized("Invalid or expired API token")
        elif self._manager is None:
            response = JSONResponse({"detail": "MCP server is not running"}, status_code=503)
        else:
            await self._manager.handle_request(scope, receive, send)
            return
        await response(scope, receive, send)


mcp_endpoint = McpEndpoint()


def mount_mcp(app: Any) -> None:
    """Serve the MCP endpoint at /mcp on `app` (the FastAPI API)."""
    global _api_app
    _api_app = app
    app.router.routes.append(
        Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"])
    )
