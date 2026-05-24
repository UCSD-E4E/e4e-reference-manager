import type { ImportResult, Item, ItemList, Library, User } from "./types";

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
};
