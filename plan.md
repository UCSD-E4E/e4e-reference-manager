# e4e Reference Manager — Plan

A self-hosted, collaborative reference manager for a research org (25–200 users) with
version history, BibTeX/Overleaf export, PDF storage, and **local/self-hosted** ML for
search and categorization.

Status: planning. Greenfield. Target deployment: a single Docker host (docker-compose),
designed to stay cloud/cluster-portable.

---

## 1. Goals & non-goals

**Goals**
- Many people contribute to a shared library; private + group-shared collections.
- Full **version history** — who changed what, when; restore previous versions.
- **Overleaf export** — per-collection `.bib`/BibLaTeX (a downloadable copy is enough).
- Store and serve **PDFs**.
- **Local ML/LLM** for semantic search, auto-tagging/categorization, summaries — no
  data leaves our infrastructure, no per-call cost.
- Open codebase that the team can contribute to.

**Non-goals (for now)**
- Two-way Overleaf sync (one-way / on-demand export only).
- Real-time collaborative editing of a single record (Google-Docs style). We do
  optimistic concurrency + history, not OT/CRDT.
- A *native* desktop/iOS/Android app. We ship a **responsive, installable PWA** instead
  — phone-friendly browse/search/read, installable to the home screen. Capture via the
  Web Share Target API on mobile + a bookmarklet on desktop.
- Full offline use. PWA offline is scoped to **read-only cached metadata**; no offline
  PDF download or offline editing initially.
- Public multi-tenant SaaS. One org, internal groups.

---

## 2. Landscape: why build (and what to reuse)

A scan of existing tools (May 2026) found **nothing that hits all five requirements**
— web-based org-wide collaboration + real version history + Overleaf export + PDF
storage + self-hosted ML. Summary:

| Tool | Web/multi-user | Self-host | BibTeX/Overleaf | History | PDFs | Local ML | Verdict |
|---|---|---|---|---|---|---|---|
| **JabRef** (MIT) | desktop only | ✅ | ✅ native | thin | ✅ | ✅ Ollama/GPT4All | Closest; no browser UI, RAM-bound, weak history |
| **Zotero** + self-host server | ✅ (web lib) | ⚠️ fragile¹ | via Better BibTeX | thin | ✅ | plugins | Great data model + importers; risky server self-host |
| **Wikindx** (since 2003) | ✅ | ✅ | ✅ | weak | ✅ | ❌ | Dated PHP, no ML |
| **I,Librarian Pro** (Nov 2025) | ✅ | ✅ | weak | weak | ✅ | ❌ | Proprietary; PDF-mgmt focus |
| **BibSonomy** | ✅ | ✅ | ✅ | ❌ | partial | ❌ | Older social-bookmark model |
| **Paperless-ngx / Teedy** | ✅ | ✅ | ❌ | ✅ docs | ✅ | OCR/search | Document managers, not reference managers |

¹ The main Zotero self-host project (ZotPrime) was **archived Aug 2025**; remaining
forks are community-maintained and brittle. Not safe to build org infra on.

**Decision: build a new web app, but reuse mature open components rather than reinvent
the hard parts.** The genuinely hard, undifferentiated work — metadata importers,
PDF parsing, citation formatting, embeddings/LLM runtime — already exists as
self-hostable open source. We build the missing piece: a collaborative, browser-based,
versioned, RBAC web app that ties them together.

**Reused components**
- **Zotero translation-server** (AGPL) — 600+ importers (DOI, arXiv, PubMed, publisher
  sites, PDF). Don't write importers by hand.
- **GROBID** — PDF → structured header metadata + parsed references.
- **citeproc / citation-js + CSL styles** — BibTeX/BibLaTeX + formatted-citation output.
- **pgvector + Ollama + sentence-transformers** — self-hosted embeddings + LLM runtime.

(If we later decide the build cost is too high, the fallback is "adopt JabRef + shared
Postgres + Ollama + git-export-to-Overleaf" — ~80% of the value, near-zero build, but a
desktop-app workflow and thin history. Documented here as the escape hatch.)

---

## 3. Architecture

A **self-hosted web app** is the core (browser access for 25–200 people, central data,
real RBAC and history). Overleaf is served by **on-demand `.bib` export**, with an
optional git mirror later.

```
                         ┌──────────────────────────────┐
        Browser ───────▶ │  Reverse proxy (Caddy/Traefik)│  TLS
                         └───────────────┬──────────────┘
                                         │
                    ┌────────────────────┼─────────────────────┐
                    ▼                    ▼                     ▼
              React/TS SPA          FastAPI API           Background workers
              (served static)   (auth, CRUD, search,      (arq/Celery + Redis):
                                 export, RBAC)             import, OCR, embed,
                                         │                  GROBID, LLM tag
            ┌──────────────┬─────────────┼───────────┬──────────────┬─────────────┐
            ▼              ▼             ▼            ▼              ▼             ▼
        Postgres     SeaweedFS       Ollama        GROBID    translation-     Redis
       + pgvector   (PDF/files,    (local LLM,   (PDF→meta)  server (Zotero  (queue +
     (metadata,      S3 API)       embeddings)               importers)      cache)
      FTS, vectors,
      audit log)
```

**Why these picks**
- **Web app + on-demand export** over pure git-backed or desktop: 25–200 mixed-skill
  users need browser access, server-side semantic search over the *whole* org library,
  and RBAC for shared/private collections — none of which a git-of-bibfiles or a
  per-user desktop app delivers well. Overleaf still gets exactly what it wants (`.bib`).
- **Single docker-compose stack**, GPU-optional. CPU embeddings are fine; GPU only
  speeds the LLM. Every service is a container; the same images run on the org's
  **Kubernetes cluster** (with the available GPU) when we scale — split workers, move
  Postgres/object-store to cluster-grade backends. We depend only on the **S3 API**, so
  the object store is swappable with no code change.

---

## 4. Tech stack (recommended)

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | The local-ML ecosystem (embeddings, GROBID clients, transformers, RAG) is overwhelmingly Python; async API, great typing/OpenAPI. |
| Frontend | **React + TypeScript + Vite, as a responsive PWA** | Standard, fast to staff; rich tables/PDF viewers. Mobile-first responsive layout + installable PWA (service worker via `vite-plugin-pwa`) so it works on phones — no separate native app. |
| DB | **Postgres 16 + pgvector** | One engine for metadata, full-text search (tsvector), **and** vector search — fewer moving parts than a separate vector DB. |
| Object storage | **SeaweedFS** (S3 API) | Self-hostable PDFs; Apache-2.0, great with many files, runs on one host *and* scales to the kube cluster (no migration). **MinIO was archived Feb 2026 — avoid.** App depends only on the S3 API, so Garage (lighter) or Ceph/Rook-RGW (if the cluster already runs Ceph) are drop-in swaps. |
| Queue/cache | **Redis + arq** (or Celery) | Background ingestion/embedding/OCR; keep API responsive. |
| LLM/embeddings | **Ollama** + **sentence-transformers** (e.g. `bge-small`/`e5`) | Fully local; matches the self-hosted-ML requirement. |
| PDF metadata | **GROBID** | Best-in-class header + reference extraction. |
| Imports | **Zotero translation-server** | 600+ importers for free. |
| Citations/export | **citeproc-py / citation-js + CSL** | BibTeX/BibLaTeX + any citation style. |
| Auth | **OIDC via Authentik** | The org runs Authentik as its IdP; app is an OIDC client (Authorization Code + PKCE). Authentik handles users/MFA/groups; we map Authentik groups → app groups/roles. |
| Canonical metadata | **CSL-JSON** | Maps cleanly to/from BibTeX and is what Zotero translators emit. |

---

## 5. Data model (core entities)

Canonical bibliographic format is **CSL-JSON**, stored in a JSONB column (round-trips to
BibTeX/BibLaTeX via citeproc).

- **User** — identity from Authentik (OIDC), app role.
- **Group** — a team; users belong to many. Sourced from Authentik group claims (groups
  don't exist there yet — we design the set and provision them; see §10).
- **Library (= Project)** — top-level container owned by a user *or* a group; the unit
  of sharing. A "project" in lab terms is a Library.
- **Collection** — nestable folders within a Library (Zotero-style).
- **Item / Reference** — the bibliographic record (`csl_json` JSONB, `type`, key fields
  denormalized for search: title, year, DOI, authors). Belongs to one Library, assigned
  to 0+ Collections. Stores a **`citation_key`** (preserved verbatim on import; unique
  *within* a Library) and a **`source_file`** label (which `.bib` it came from).
- **BibFile** — a source `.bib` belonging to a Library (a project may have **1..N**).
  Tracks original filename so we can re-export the same per-file partitioning.
- **Attachment** — file (PDF, etc.) linked to an Item; stored in the object store; **content-hash
  dedup**; extracted plain text cached for FTS.
- **Tag** — manual or ML-suggested (flag the source + confidence).
- **Note** — free-text (Markdown) note on an Item; authored by a user, multiple per
  Item, changes captured in the audit log.
- **Annotation** — highlight/comment **anchored to a location in a PDF Attachment**
  (Phase 4); distinct from a Note.
- **Embedding** — vector(s) per Item/chunk in pgvector for semantic search + RAG.
- **AuditEvent** — append-only: actor, timestamp, entity, operation, JSON patch
  (before/after). The backbone of history.

**Access control:** roles `admin | member | read-only`; Libraries/Collections shared
with Groups at `view | edit | manage` levels. All queries are permission-scoped.

---

## 6. Key subsystems

**Version history (a hard requirement).** Source of truth = an **append-only
`AuditEvent` log** capturing every mutation as a JSON patch. From it we get: per-record
timeline ("who changed the abstract on May 3"), restore-to-version, and an org activity
feed. Optionally layer Postgres system-versioned snapshots for fast point-in-time
queries. The optional **git mirror** (Phase 4) gives a second, human-diffable history in
plain `.bib` — and doubles as the Overleaf feed.

**PDF ingestion.** Upload → object store (dedup by SHA-256) → worker extracts text (PyMuPDF) →
GROBID parses header metadata + references → propose/merge into the Item → embed for
search. Drag-drop a PDF and get a populated record.

**PDF viewing (in-app).** We render PDFs **inside the app** with **PDF.js** (`react-pdf`),
not the browser's native viewer — consistent on desktop *and* mobile/PWA, with in-app
text selection/search and a foundation for annotations. PDFs stream via **HTTP range
requests** from a **signed, short-expiry URL** (members-only, issued after an auth check),
so phones fetch pages on demand instead of the whole file. The same signed URL backs a
first-class **"Download original PDF"** action (and the "open in OS viewer" escape hatch
if PDF.js is too heavy on an old device). **Highlights / annotations** (anchored to PDF
locations, stored in the `Annotation` entity) come in Phase 4.

**Metadata import.** Paste a DOI/arXiv ID/URL → translation-server resolves → CSL-JSON →
new Item. **Bulk `.bib` import** for migrating existing libraries: a project (Library)
can ingest **multiple `.bib` files**, each tracked as a `BibFile`. **Citation keys are
preserved verbatim**; on a within-Library key collision we flag it for the user rather
than silently rewriting. (RIS/Zotero-RDF importers too.)

**Search.**
1. *Keyword/full-text* — Postgres `tsvector` over title/abstract/notes/PDF text.
2. *Semantic* — local embeddings in pgvector, ANN search; hybrid-rank with keyword.
3. *LLM categorization* — Ollama zero/few-shot tagging into the org taxonomy, plus
   duplicate-detection and merge suggestions. Batchable on CPU; faster on GPU.

**Overleaf / BibTeX export.** On-demand `.bib`/`.biblatex` — export a whole Library, a
Collection, or **a single source `BibFile`** (so a project's per-document `.bib` files
round-trip unchanged). Preserved `citation_key`s are reused so existing `.tex` `\cite{}`
calls keep working. Configurable CSL style. Download or pull via a stable URL.
Phase 4: optional continuous git mirror pushed to an Overleaf project via its git bridge.

**Mobile / PWA.** The SPA is a **responsive, installable PWA** (service worker via
`vite-plugin-pwa`): mobile-first layouts, add-to-home-screen, cached app shell. On
phones, the **Web Share Target API** lets you share a paper URL/DOI from the browser
straight into the app → translation-server ingests it (no extension needed). Offline is
**read-only cached metadata** only — no offline PDFs/editing for now. iOS grants full
PWA capabilities only after the user installs to the home screen, so we design for that.

---

## 7. Deployment (single Docker host → Kubernetes later)

`docker-compose.yml` services: `proxy` (Caddy, auto-TLS), `web`, `api`, `worker`,
`postgres` (pgvector image), `redis`, `seaweedfs` (S3 API), `ollama`, `grobid`,
`translation-server`. Authentik is assumed to already run as the org IdP (external to
this stack); the app registers as an OIDC client.

- **Start CPU-only.** Everything runs without a GPU (embeddings are fast on CPU; LLM
  tagging/summaries run slower or as nightly batch jobs). This is the day-one mode.
- **Scale to the cluster.** When load warrants, the same images deploy to the org's
  **Kubernetes cluster** and Ollama is scheduled onto the **available GPU node** for
  interactive LLM features. On kube, SeaweedFS scales out — or point the app at the
  cluster's existing S3 endpoint (e.g. Ceph/Rook RGW) since we only use the S3 API.
- **Sizing (start):** 8–16 GB RAM, 4+ cores; GROBID likes ~4 GB; storage scales with
  PDFs (budget for it on the SeaweedFS volume).
- **Backups:** nightly `pg_dump` + object-store replication/snapshot. The audit log +
  object store are the crown jewels.
- **Config** via `.env`; secrets out of the repo.

---

## 8. Security & privacy

- **Authentik OIDC** + session cookies; CSRF protection; per-resource authorization
  checks. Authentik groups map to app groups/roles.
- All ML is local (Ollama/embeddings) — no document content leaves our infrastructure.
- **PDFs are lab-only.** Stored PDFs (incl. publisher copies) are accessible **only to
  authenticated lab members** — never public, no anonymous links. Still worth a short
  internal acceptable-use note, but access policy is settled: members-only.
- Signed, short-expiry URLs for PDF downloads from the object store.
- Audit log is tamper-evident (append-only; no in-place edits/deletes via the app).

---

## 9. Roadmap (phased)

Each phase ends in something usable.

- **Phase 0 — Foundations.** Repo, docker-compose, Postgres schema, Authentik OIDC
  login, CRUD on references (manual entry), **bulk `.bib` import** (the lab already has
  `.bib` files — migrate them day one) + `.bib` export, PDF upload to the object store
  + **in-app PDF.js viewer**. **Responsive layout + installable PWA shell** from the
  start (test on a phone).
  → *A working reference manager with the existing libraries loaded, readable on mobile.*
- **Phase 1 — Collaboration & history.** Groups, Libraries, Collections, RBAC,
  shared/private sharing, **item Notes** (Markdown, per-author), the **AuditEvent** log
  + restore + activity feed.
  → *The core differentiator: contribute + notes + history.*
- **Phase 2 — Ingestion automation.** translation-server (DOI/arXiv/URL), GROBID PDF→
  metadata, dedup/merge. **Mobile capture via Web Share Target** (share a URL/DOI into
  the PWA → ingest).
  → *Adding papers is fast, including from a phone.*
- **Phase 3 — Search & local ML.** Postgres FTS, pgvector embeddings + hybrid semantic
  search, Ollama auto-tagging/categorization + summaries.
  → *Find and organize papers intelligently, fully local.*
- **Phase 4 — Overleaf & polish.** Per-collection/per-`BibFile` `.bib` + CSL styles,
  optional git mirror → Overleaf push, desktop capture bookmarklet, **offline read-only
  caching** in the PWA, **PDF highlights/annotations** (anchored, stored in `Annotation`),
  RAG "chat with collection."

**MVP for the team = Phases 0–1** (collaborative, versioned, manual entry + export).
Phases 2–3 deliver the "wow." Phase 4 is convenience.

---

## 10. Resolved decisions & remaining questions

**Resolved (2026-05-23):**
- **SSO/IdP → Authentik.** App is an OIDC client; map Authentik groups → app roles.
- **PDF policy → lab-only.** Store PDFs incl. publisher copies, restricted to
  authenticated lab members; no public/anonymous access.
- **Existing libraries → yes, `.bib` files exist.** Bulk `.bib` import is a Phase 0 task
  (migrate day one), not deferred to Phase 2.
- **Hardware → start CPU-only; GPU on the kube cluster when scaling.** Design stays
  kube-portable; Ollama moves to the GPU node later.
- **Ops/backups → owner exists.** A person owns the host + backups/upgrades.

- **Authentik groups → greenfield.** No groups exist yet; we **design the group/role
  taxonomy and provision it in Authentik**, then map group claims → app roles
  (`admin/member/read-only`) + Library sharing. (Design task in Phase 1.)
- **`.bib` per project → 1..N.** A project (Library) can hold multiple source `.bib`
  files, each tracked as a `BibFile` for faithful per-file re-export.
- **Citation keys → preserved** verbatim on import; collisions within a Library flagged.
- **Concurrency → optimistic locking** (no live co-editing); history covers the rest.

**Still open (design tasks, not blockers):**
1. **Group/role taxonomy** to create in Authentik (e.g. per-lab-subteam groups, an
   admins group). Define in Phase 1.
2. **`.bib` migration logistics** — collect the projects' files; confirm whether any
   reference local PDF paths to pull in during import.

---

## 11. Suggested repo structure

```
e4e-reference-manager/
├── docker-compose.yml
├── .env.example
├── README.md
├── plan.md                 # this file
├── api/                    # FastAPI backend
│   ├── app/ (models, routers, services, workers, ml/)
│   ├── migrations/         # alembic
│   └── tests/
├── web/                    # React + TS (Vite)
└── deploy/                 # Caddyfile, init scripts, backup cron
```

---

## 12. Immediate next steps

1. Close the remaining questions in §10 (Authentik groups, `.bib` specifics, key policy).
2. `git init` + scaffold the repo structure above.
3. Stand up docker-compose with Postgres+pgvector, SeaweedFS, FastAPI hello-world, and
   wire Authentik OIDC login.
4. Define the Postgres schema + CSL-JSON model; build Item CRUD + bulk `.bib`
   import/export (load the lab's existing libraries).
5. Begin Phase 1 (groups, RBAC, audit log) — the core differentiator.
