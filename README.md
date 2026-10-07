# e4e Reference Manager

A self-hosted, collaborative reference manager for a research org: version history,
BibTeX/Overleaf export, in-app PDF reading, and (later) local ML for search and
categorization. See [plan.md](plan.md) for the full design and roadmap.

**Status: Phase 2 (Ingestion automation) — implemented; live services need `docker compose up`.**

## Stack

- **API** — FastAPI (Python), async SQLAlchemy, Postgres + pgvector
- **Object storage** — SeaweedFS (S3 API) for PDFs
- **Web** — React + TypeScript + Vite, as an installable PWA, with an in-app PDF.js viewer
- **Auth** — Authentik OIDC (with a dev-bypass mode for local development)

## Quick start

Requires Docker + Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

Then open:

| Service | URL |
|---|---|
| Web app (PWA) | http://localhost:5173 |
| API docs (Swagger) | http://localhost:8000/docs |
| SeaweedFS S3 | http://localhost:8333 |

By default `REFMAN_DEV_AUTH=true`, so you're auto-logged-in as a dev user — no Authentik
needed to try it locally.

## Develop in a dev container (recommended for contributors)

The repo ships a [VS Code Dev Container](.devcontainer/). In VS Code with the **Dev
Containers** extension: **“Reopen in Container.”** This brings up the whole stack
(Postgres, SeaweedFS, API, web) and drops you into a tooling container with pinned
**Python 3.12 + uv** and **Node 22**, so IntelliSense/linting/tests work for both the
API and the frontend with zero host setup. (No need to install Python or Node on your
machine — handy, since host Python/Node versions often don't match the project.)

It's compose-integrated: the app keeps running in its own `api`/`web` services
(http://localhost:5173, http://localhost:8000), and you edit/run tests in the `dev`
service. `cp .env.example .env` first if you haven't.

> **Host-specific note:** the dev container currently mounts the repo at its host
> absolute path (`/home/chris/...`) and bind-mounts `~/.claude` so Claude Code's auth,
> settings, and per-project memory carry over unchanged. Both the workspace path
> (`.devcontainer/devcontainer.json` + `docker-compose.dev.yml`) are pinned to that
> path. **When other people start contributing**, switch the workspace mount back to a
> generic `/workspaces/${localWorkspaceFolderBasename}` and drop/adjust the `~/.claude`
> mount, since those are personal to one machine.

## What works in Phase 0

- Create **Projects** (Libraries) and add references manually.
- **Import `.bib` files** (multiple per project, tracked as `BibFile`s). Citation keys are
  **preserved verbatim**; within-project key collisions are **flagged**, not rewritten.
- **Export `.bib`** for a whole project (per-`BibFile` export endpoint also available).
- **Upload PDFs** (stored in SeaweedFS, deduped by SHA-256).
- **View PDFs in-app** via PDF.js and **download** the original via a signed,
  short-expiry URL.
- **Edit references** with optimistic locking (version conflicts return HTTP 409).
- Installable **PWA** shell with a responsive, mobile-first layout.

## What works in Phase 1 (collaboration & history)

- **Groups** and membership; libraries owned by a **user or a group**.
- **RBAC**: per-library access levels (view / edit / manage) via ownership, group
  ownership, or shares; org admins see everything. All read/write paths are
  permission-scoped (no access → 404).
- **Sharing**: share a library with groups at a chosen access level.
- **Notes**: per-author Markdown notes on references.
- **History**: append-only audit log of every change, an **item history view with
  one-click restore**, and a per-library **activity feed**.
- **Authentik group sync**: OIDC login maps group claims → memberships and an admin
  group → org-admin.
- Schema is managed by **Alembic** (the API runs `alembic upgrade head` on start).

## What works in Phase 2 (ingestion automation)

- **Import by identifier/URL** — paste a DOI, arXiv ID, PMID, ISBN, or URL; metadata
  is fetched via the **Zotero translation-server** (600+ translators) and added as a
  reference, with **dedup by DOI** within the library.
- **PDF → metadata** — **GROBID** extracts title/authors/DOI/abstract/year from a PDF;
  "Add from PDF" creates a reference from an upload, and "Fill metadata from PDF"
  populates an existing one.
- **Merge** duplicate references (moves PDFs + notes, then removes the duplicate).
- **Mobile capture** — the PWA's Web Share Target lets you share a URL/DOI from a phone
  straight into a project.

> Phase 2 adds two services to `docker-compose.yml`: `translation-server` (light) and
> `grobid` (heavy, ~4 GB RAM). Bring them up with `docker compose up -d` (GROBID takes a
> minute to load models). translation-server needs outbound internet to fetch metadata.

## What works in Phase 3 (search & local ML)

- **Full-text search** — `GET /libraries/{id}/items?q=` is Postgres FTS over **title,
  abstract, authors, and PDF body text** (stemmed, case-insensitive), ranked title >
  metadata > PDF body. PDF text is extracted with **PyMuPDF** on upload and cached on the
  attachment (re-run with `POST /attachments/{id}/extract-text`).
- **Semantic + hybrid search** — `GET /libraries/{id}/search?q=&mode=keyword|semantic|hybrid`.
  Each reference is embedded (title+abstract) via **Ollama** into **pgvector**; semantic
  mode is cosine-distance ANN (HNSW index), hybrid fuses keyword + semantic with
  reciprocal-rank fusion. `POST /libraries/{id}/reindex` backfills embeddings (after a
  bulk import, or once Ollama is available).
- **LLM auto-tagging & summaries** — `POST /items/{id}/suggest-tags` (add `?apply=true`
  to persist them as `ml`-sourced tags), `GET /items/{id}/tags`, and
  `POST /items/{id}/summary`. Backed by Ollama (`qwen2.5:3b`).

> Phase 3 adds the **`ollama`** service. Everything degrades gracefully if Ollama is
> down (keyword search keeps working; tags/summaries return empty), but to enable the ML
> features pull the models once (needs outbound internet):
>
> ```bash
> docker compose exec ollama ollama pull nomic-embed-text   # embeddings (768-dim)
> docker compose exec ollama ollama pull qwen2.5:3b          # tagging + summaries
> ```
>
> Config knobs (`.env`): `REFMAN_OLLAMA_URL`, `REFMAN_EMBEDDING_MODEL`,
> `REFMAN_EMBEDDING_DIM` (must match the model and the `item.embedding` column width —
> changing it needs a migration + reindex), `REFMAN_LLM_MODEL`.

## What works in Phase 4 (PDF annotations)

- **Highlights & comments on PDFs** — select text in the in-app PDF.js viewer and click
  "Highlight selection" to anchor a colored highlight (with an optional comment) to that
  spot. Anchors are stored as a page number + normalized rectangles (0..1), so they hold
  up across zoom/width. Highlights render as an overlay and are listed under the viewer
  (jump-to-page + delete). Backed by the `annotation` table and
  `GET/POST /attachments/{id}/annotations`, `PATCH/DELETE /annotations/{id}`.
- **RBAC**: library `view` to read, `edit` to create, author-or-manager to edit/delete;
  every change is written to the audit log (`entity_type=annotation`).
- **Collections** — group items into (nestable) folders within a library and **export a
  single collection to `.bib`** (in addition to whole-library and per-source-`BibFile`
  export). Create/manage collections in the project view; assign an item from its page.
  `GET/POST /libraries/{id}/collections`, `POST/DELETE /collections/{id}/items/{itemId}`,
  `GET /collections/{id}/export.bib`.

## What works in Phase 5 (source validation / anti-hallucination)

LLM-generated bibliographies often include **fabricated citations** — plausible-looking
references whose DOIs don't resolve or point to a different paper. The app can now
verify each reference against canonical registrars:

- **DOI** → Crossref `/works/{doi}` (404 ⇒ likely fabricated; resolves but title differs
  ⇒ the DOI is real but the citation is wrong).
- **arXiv id** → arXiv API.
- **No identifier** → Crossref title/author search; verified only if the top hit's title
  closely matches.

Verdicts (`verified` ✓, `metadata_mismatch` ⚠, `not_found` ✗, `unverifiable` ?) are
cached on the item, shown as a badge in the project view, and detailed on the item
page. Endpoints: `POST /items/{id}/validate` (one item), `POST /libraries/{id}/validate`
(batch — returns a count summary).

**Scan a PDF's bibliography for fabricated citations.** GROBID's `/api/processReferences`
extracts every cited work in the PDF; each is then run through the same Crossref/arXiv
check. On an item with a PDF attachment, click **"Verify citations in this PDF"** to get
a per-citation verdict table (and a `{verified, mismatch, not_found, unverifiable}`
summary). Endpoints: `POST /items/{id}/validate-references` (uses the attached PDF),
`POST /pdf-validate` (multipart upload — vet an AI-drafted paper before importing).

## What works in Phase 7 (JabRef-style auto-groups)

Live, rule-driven groups within a library — membership is **recomputed on demand**, so
new items matching the rule appear automatically. Three rule kinds:

- **By field value** — one group per unique value of `year` / CSL `type` / `journal` /
  any-author family. Generate them in one click ("Generate by year" creates a group per
  year present in the library; calls are idempotent).
- **By tag** — items carrying a specific Tag, optionally filtered by source
  (`manual` / `ml`). The **"From ML-suggested tags"** generator turns every Phase-3d
  ml-tag into its own auto-group, so you can browse what the LLM thinks belongs
  together.
- **By saved search** — name the current search box query; an auto-group whose members
  are whatever currently matches that FTS query.

Each auto-group exports its current membership to `.bib`. Endpoints:
`GET/POST /libraries/{id}/auto-groups`, `POST /libraries/{id}/auto-groups/generate`,
`GET /auto-groups/{id}/items`, `GET /auto-groups/{id}/export.bib`,
`DELETE /auto-groups/{id}`.

> Validation hits external services (api.crossref.org, export.arxiv.org). Crossref's
> polite pool is used (no API key required); the User-Agent identifies the app per
> Crossref guidance.

## Enabling Authentik (production auth)

Set in `.env`:

```bash
REFMAN_DEV_AUTH=false
REFMAN_OIDC_ISSUER=https://authentik.example.org/application/o/refman/
REFMAN_OIDC_CLIENT_ID=...
REFMAN_OIDC_CLIENT_SECRET=...
REFMAN_OIDC_REDIRECT_URI=http://localhost:8000/auth/callback
```

Then the SPA's “login” flow redirects through Authentik. (Group/role mapping and a
single-origin Caddy reverse proxy land in Phase 1.)

## Production deployment (bib.krg.ucsd.edu)

Production runs as the KRG Incus tenant `reference-manager`. krg-infra owns the platform
(the slot, secrets in OpenBao, the Authentik app, the public route); this repo owns
everything inside the slot:

- `flake.nix` is the tenant's NixOS config (`mkTenant`), pinned to krg-infra by `flake.lock`.
- `deploy/incus/` holds the compose stack and the inner Traefik. It serves one origin:
  `/api/*` → API (prefix stripped, `--root-path /api`), everything else → the static web
  image (`web/Dockerfile`). PDFs live in the e4e-nas Garage bucket `reference-manager`,
  reached at `https://s3.e4e.ucsd.edu`.
- Releasing: tag `vX.Y.Z`. `release.yml` pushes the images to GHCR and opens an
  `auto-deploy/vX.Y.Z` PR that bumps the pins; merging it deploys via `deploy.yml`.

Full hand-off: krg-infra `docs/handoff/reference-manager/HANDOFF.md` and
`docs/onboarding-reference-manager.md`.

## Project layout

```
api/            FastAPI backend (app/, Dockerfile, pyproject.toml)
  app/
    models.py   SQLAlchemy models
    routers/    libraries, items, search, ml, bib, attachments, ingest, collab, notes, auth, health
    bibtex.py   BibTeX <-> CSL-JSON conversion
    storage.py  S3/SeaweedFS helpers
    pdf.py      PDF text extraction (PyMuPDF)
    embeddings.py  Ollama embeddings for semantic search
    llm.py      Ollama auto-tagging + summaries
web/            React + TS + Vite PWA (src/, vite.config.ts)
deploy/         service configs (SeaweedFS S3 identity)
docker-compose.yml
plan.md         design & roadmap
```

## Tests

Backend tests use pytest + pytest-asyncio against an ASGI client and a dedicated
`refman_test` Postgres DB (created/dropped automatically; external services are
monkeypatched). From inside the dev container:

```bash
cd api && uv run pytest          # full suite
uv run pytest tests/test_unit.py # fast, no DB
```

**We work test-first (TDD) from Phase 3 on:** write a failing test, implement to green,
then refactor. Run the suite before committing each milestone.

## Notes for developers

- **Schema** is managed by **Alembic**; the API container runs `alembic upgrade head`
  on start. Add a migration with `uv run alembic revision --autogenerate -m "…"`.
- **API & web** hot-reload in Compose (source is bind-mounted).
- The frontend talks to the API at `VITE_API_URL` (default `http://localhost:8000`) with
  CORS + credentials. A single-origin Caddy setup comes with production hardening.
