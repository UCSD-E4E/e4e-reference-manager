# e4e Reference Manager

A self-hosted, collaborative reference manager for a research org: version history,
BibTeX/Overleaf export, in-app PDF reading, and (later) local ML for search and
categorization. See [plan.md](plan.md) for the full design and roadmap.

**Status: Phase 0 (Foundations) — implemented & verified.**

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

## Project layout

```
api/            FastAPI backend (app/, Dockerfile, pyproject.toml)
  app/
    models.py   SQLAlchemy models
    routers/    libraries, items, bib import/export, attachments, auth, health
    bibtex.py   BibTeX <-> CSL-JSON conversion
    storage.py  S3/SeaweedFS helpers
web/            React + TS + Vite PWA (src/, vite.config.ts)
deploy/         service configs (SeaweedFS S3 identity)
docker-compose.yml
plan.md         design & roadmap
```

## Notes for developers

- **Schema**: Phase 0 bootstraps tables via SQLAlchemy `create_all` on startup. Alembic
  migrations are introduced when the schema stabilizes (Phase 1).
- **API & web** hot-reload in Compose (source is bind-mounted).
- The frontend talks to the API at `VITE_API_URL` (default `http://localhost:8000`) with
  CORS + credentials. A single-origin Caddy setup comes with production hardening.
