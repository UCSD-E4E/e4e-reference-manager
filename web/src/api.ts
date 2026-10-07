import type {
  Annotation,
  AnnotationRect,
  AuditEvent,
  AutoGroup,
  AutoGroupGenerateSource,
  AutoGroupKind,
  Collection,
  DedupeResult,
  GenerateResult,
  Group,
  ImportResult,
  IngestResult,
  Item,
  ItemList,
  Library,
  Note,
  PastePreviewEntry,
  PdfValidationResult,
  SearchMode,
  Share,
  SuggestedTags,
  Tag,
  User,
  Validation,
} from "./types";

export const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

const LOGIN_REDIRECT_KEY = "refman-login-redirect-at";
// Set once this page load has started navigating to login, so concurrent 401s (a page
// firing several requests at once) join that redirect instead of tripping the guard.
let redirectingToLogin = false;

/** Send a logged-out visitor through the API's OIDC login, returning to this page.
 * Returns false (show the error instead) if an EARLIER page load redirected moments
 * ago, so a session cookie that fails to stick can't loop the browser through Authentik. */
function redirectToLogin(): boolean {
  if (redirectingToLogin) return true;
  try {
    const last = Number(sessionStorage.getItem(LOGIN_REDIRECT_KEY) ?? 0);
    if (Date.now() - last < 15_000) return false;
    sessionStorage.setItem(LOGIN_REDIRECT_KEY, String(Date.now()));
  } catch {
    /* storage unavailable: redirect anyway */
  }
  redirectingToLogin = true;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`${API_URL}/auth/login?next=${encodeURIComponent(next)}`);
  return true;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: init?.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json", ...(init?.headers ?? {}) }
      : init?.headers,
    ...init,
  });
  // Only reads redirect: navigating away on a failed write (POST/PATCH/…) would throw
  // away whatever the user was saving. Writes surface the 401 so the form keeps its data.
  // A redirecting read never settles, so the page doesn't flash the error meanwhile.
  const isRead = ["GET", "HEAD"].includes((init?.method ?? "GET").toUpperCase());
  if (res.status === 401 && isRead && redirectToLogin()) return new Promise<T>(() => {});
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    if (res.status === 401 && !isRead) {
      detail += " — your session expired. Sign in again in a new tab, then retry; your changes are still here.";
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  me: () => req<User>("/auth/me"),

  listLibraries: () => req<Library[]>("/libraries"),
  createLibrary: (name: string, description = "") =>
    req<Library>("/libraries", { method: "POST", body: JSON.stringify({ name, description }) }),
  getLibrary: (id: string) => req<Library>(`/libraries/${id}`),
  deleteLibrary: (id: string) => req<void>(`/libraries/${id}`, { method: "DELETE" }),

  listItems: (libId: string, q = "") =>
    req<ItemList>(`/libraries/${libId}/items${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  searchItems: (libId: string, q: string, mode: SearchMode) =>
    req<ItemList>(`/libraries/${libId}/search?q=${encodeURIComponent(q)}&mode=${mode}`),
  reindexLibrary: (libId: string) =>
    req<{ items: number; embedded: number }>(`/libraries/${libId}/reindex`, { method: "POST" }),
  getItem: (id: string) => req<Item>(`/items/${id}`),
  createItem: (libId: string, citation_key: string, type: string, csl_json: object) =>
    req<Item>(`/libraries/${libId}/items`, {
      method: "POST",
      body: JSON.stringify({ citation_key, type, csl_json }),
    }),
  updateItem: (id: string, version: number, csl_json: object) =>
    req<Item>(`/items/${id}`, { method: "PATCH", body: JSON.stringify({ version, csl_json }) }),
  deleteItem: (id: string) => req<void>(`/items/${id}`, { method: "DELETE" }),

  previewPaste: (libId: string, bibtex: string) =>
    req<{ entries: PastePreviewEntry[] }>(`/libraries/${libId}/import/preview`, {
      method: "POST",
      body: JSON.stringify({ bibtex }),
    }),
  importPaste: (libId: string, bibtex: string, indices: number[]) =>
    req<ImportResult>(`/libraries/${libId}/import/text`, {
      method: "POST",
      body: JSON.stringify({ bibtex, indices }),
    }),
  dedupeLibrary: (libId: string, dryRun: boolean) =>
    req<DedupeResult>(`/libraries/${libId}/dedupe?dry_run=${dryRun}`, { method: "POST" }),
  importBib: (libId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<ImportResult>(`/libraries/${libId}/import`, { method: "POST", body: fd });
  },
  exportLibraryUrl: (libId: string) => `${API_URL}/libraries/${libId}/export.bib`,

  // Collections (Phase 4)
  listCollections: (libId: string) => req<Collection[]>(`/libraries/${libId}/collections`),
  createCollection: (libId: string, name: string, parent_id: string | null = null) =>
    req<Collection>(`/libraries/${libId}/collections`, {
      method: "POST",
      body: JSON.stringify({ name, parent_id }),
    }),
  deleteCollection: (id: string) => req<void>(`/collections/${id}`, { method: "DELETE" }),
  addItemToCollection: (collId: string, itemId: string) =>
    req<void>(`/collections/${collId}/items/${itemId}`, { method: "POST" }),
  removeItemFromCollection: (collId: string, itemId: string) =>
    req<void>(`/collections/${collId}/items/${itemId}`, { method: "DELETE" }),
  exportCollectionUrl: (id: string) => `${API_URL}/collections/${id}/export.bib`,

  // Auto-groups (Phase 7)
  listAutoGroups: (libId: string) => req<AutoGroup[]>(`/libraries/${libId}/auto-groups`),
  createAutoGroup: (
    libId: string,
    body: { name: string; kind: AutoGroupKind; params: Record<string, unknown> },
  ) =>
    req<AutoGroup>(`/libraries/${libId}/auto-groups`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteAutoGroup: (id: string) => req<void>(`/auto-groups/${id}`, { method: "DELETE" }),
  generateAutoGroups: (libId: string, source: AutoGroupGenerateSource) =>
    req<GenerateResult>(`/libraries/${libId}/auto-groups/generate`, {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  exportAutoGroupUrl: (id: string) => `${API_URL}/auto-groups/${id}/export.bib`,

  uploadAttachment: (itemId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<Item["attachments"][number]>(`/items/${itemId}/attachments`, {
      method: "POST",
      body: fd,
    });
  },
  attachmentSignedUrl: (attId: string) =>
    req<{ url: string; content_type: string }>(`/attachments/${attId}/url`),
  attachmentDownloadUrl: (attId: string) => `${API_URL}/attachments/${attId}/download`,

  // PDF annotations (Phase 4)
  listAnnotations: (attId: string) => req<Annotation[]>(`/attachments/${attId}/annotations`),
  createAnnotation: (
    attId: string,
    body: { page: number; rects: AnnotationRect[]; color?: string; quote?: string; comment?: string },
  ) =>
    req<Annotation>(`/attachments/${attId}/annotations`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteAnnotation: (id: string) => req<void>(`/annotations/${id}`, { method: "DELETE" }),

  // Groups & members
  listGroups: () => req<Group[]>("/groups"),
  createGroup: (slug: string, name: string) =>
    req<Group>("/groups", { method: "POST", body: JSON.stringify({ slug, name }) }),
  listMembers: (groupId: string) => req<User[]>(`/groups/${groupId}/members`),
  addMember: (groupId: string, email: string) =>
    req<User[]>(`/groups/${groupId}/members`, { method: "POST", body: JSON.stringify({ email }) }),
  removeMember: (groupId: string, userId: string) =>
    req<void>(`/groups/${groupId}/members/${userId}`, { method: "DELETE" }),

  // Library shares
  listShares: (libId: string) => req<Share[]>(`/libraries/${libId}/shares`),
  upsertShare: (libId: string, group_id: string, access_level: string) =>
    req<Share>(`/libraries/${libId}/shares`, {
      method: "POST",
      body: JSON.stringify({ group_id, access_level }),
    }),
  deleteShare: (libId: string, groupId: string) =>
    req<void>(`/libraries/${libId}/shares/${groupId}`, { method: "DELETE" }),

  // Notes
  listNotes: (itemId: string) => req<Note[]>(`/items/${itemId}/notes`),
  createNote: (itemId: string, body: string) =>
    req<Note>(`/items/${itemId}/notes`, { method: "POST", body: JSON.stringify({ body }) }),
  updateNote: (noteId: string, body: string) =>
    req<Note>(`/notes/${noteId}`, { method: "PATCH", body: JSON.stringify({ body }) }),
  deleteNote: (noteId: string) => req<void>(`/notes/${noteId}`, { method: "DELETE" }),

  // History / activity
  itemHistory: (itemId: string) => req<AuditEvent[]>(`/items/${itemId}/history`),
  restoreItem: (itemId: string, eventId: string) =>
    req<Item>(`/items/${itemId}/restore?event_id=${eventId}`, { method: "POST" }),
  libraryActivity: (libId: string) => req<AuditEvent[]>(`/libraries/${libId}/activity`),

  // Group-owned library
  createGroupLibrary: (name: string, owner_group_id: string) =>
    req<Library>("/libraries", { method: "POST", body: JSON.stringify({ name, owner_group_id }) }),

  // Ingestion (Phase 2)
  ingest: (libId: string, query: string) =>
    req<IngestResult>(`/libraries/${libId}/ingest`, {
      method: "POST",
      body: JSON.stringify({ query }),
    }),
  createItemFromPdf: (libId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<Item>(`/libraries/${libId}/items/from-pdf`, { method: "POST", body: fd });
  },
  extractMetadata: (itemId: string, apply: boolean) =>
    req<{ csl_json: Record<string, unknown>; applied: boolean; item: Item | null }>(
      `/items/${itemId}/extract-metadata?apply=${apply}`,
      { method: "POST" },
    ),

  // Local ML (Phase 3): tags + summaries
  listItemTags: (itemId: string) => req<Tag[]>(`/items/${itemId}/tags`),
  // With `tags`, saves exactly those (no model call) — used to apply what was shown.
  suggestTags: (itemId: string, apply: boolean, tags?: string[]) =>
    req<SuggestedTags>(`/items/${itemId}/suggest-tags?apply=${apply}`, {
      method: "POST",
      ...(tags ? { body: JSON.stringify({ tags }) } : {}),
    }),
  summarizeItem: (itemId: string) =>
    req<{ summary: string }>(`/items/${itemId}/summary`, { method: "POST" }),

  // Source validation (Phase 5)
  validateItem: (itemId: string) =>
    req<Validation>(`/items/${itemId}/validate`, { method: "POST" }),
  validateItemReferences: (itemId: string) =>
    req<PdfValidationResult>(`/items/${itemId}/validate-references`, { method: "POST" }),
  validateLibrary: (libId: string) =>
    req<{
      checked: number;
      verified: number;
      metadata_mismatch: number;
      not_found: number;
      unverifiable: number;
    }>(`/libraries/${libId}/validate`, { method: "POST" }),
};
