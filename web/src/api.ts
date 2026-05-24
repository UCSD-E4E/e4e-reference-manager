import type {
  AuditEvent,
  Group,
  ImportResult,
  Item,
  ItemList,
  Library,
  Note,
  Share,
  User,
} from "./types";

export const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: init?.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json", ...(init?.headers ?? {}) }
      : init?.headers,
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
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

  listItems: (libId: string, q = "") =>
    req<ItemList>(`/libraries/${libId}/items${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  getItem: (id: string) => req<Item>(`/items/${id}`),
  createItem: (libId: string, citation_key: string, type: string, csl_json: object) =>
    req<Item>(`/libraries/${libId}/items`, {
      method: "POST",
      body: JSON.stringify({ citation_key, type, csl_json }),
    }),
  updateItem: (id: string, version: number, csl_json: object) =>
    req<Item>(`/items/${id}`, { method: "PATCH", body: JSON.stringify({ version, csl_json }) }),
  deleteItem: (id: string) => req<void>(`/items/${id}`, { method: "DELETE" }),

  importBib: (libId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<ImportResult>(`/libraries/${libId}/import`, { method: "POST", body: fd });
  },
  exportLibraryUrl: (libId: string) => `${API_URL}/libraries/${libId}/export.bib`,

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
};
