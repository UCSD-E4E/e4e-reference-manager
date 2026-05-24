import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import PdfViewer from "../components/PdfViewer";
import type { AuditEvent, Item, Note } from "../types";

function authorsText(csl: Record<string, unknown>): string {
  const authors = csl.author as Array<{ family?: string; given?: string }> | undefined;
  if (!authors) return "";
  return authors.map((a) => [a.given, a.family].filter(Boolean).join(" ")).join(", ");
}

export default function ItemView() {
  const { itemId = "" } = useParams();
  const [item, setItem] = useState<Item | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  const [notes, setNotes] = useState<Note[]>([]);
  const [history, setHistory] = useState<AuditEvent[]>([]);
  const [noteBody, setNoteBody] = useState("");
  const [err, setErr] = useState("");
  const [viewUrl, setViewUrl] = useState<string | null>(null);

  const reload = async () => {
    try {
      const it = await api.getItem(itemId);
      setItem(it);
      setNotes(await api.listNotes(itemId));
      setHistory(await api.itemHistory(itemId));
      const lib = await api.getLibrary(it.library_id);
      setCanEdit(lib.my_access === "edit" || lib.my_access === "manage");
    } catch (e) {
      setErr(String(e));
    }
  };
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId]);

  const onUpload = async (e: FormEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    try {
      await api.uploadAttachment(itemId, file);
      reload();
    } catch (e) {
      setErr(String(e));
    }
    e.currentTarget.value = "";
  };

  const addNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!noteBody.trim()) return;
    try {
      await api.createNote(itemId, noteBody.trim());
      setNoteBody("");
      reload();
    } catch (e) {
      setErr(String(e));
    }
  };

  const view = async (attId: string) => {
    try {
      const { url } = await api.attachmentSignedUrl(attId);
      setViewUrl(url);
    } catch (e) {
      setErr(String(e));
    }
  };

  const restore = async (eventId: string) => {
    try {
      await api.restoreItem(itemId, eventId);
      reload();
    } catch (e) {
      setErr(String(e));
    }
  };

  if (!item)
    return <p className="muted">{err ? <span className="error">{err}</span> : "Loading…"}</p>;

  const csl = item.csl_json;
  return (
    <div>
      <p className="muted">
        <Link to={`/libraries/${item.library_id}`}>← Back to project</Link>
      </p>
      <h1>{item.title || "(untitled)"}</h1>
      {err && <p className="error">{err}</p>}

      <div className="card">
        <div className="muted">
          <span className="tag">{item.type}</span> {item.citation_key}
          {item.year ? ` · ${item.year}` : ""}
        </div>
        {authorsText(csl) && <p>{authorsText(csl)}</p>}
        {typeof csl["container-title"] === "string" && (
          <p className="muted">{csl["container-title"] as string}</p>
        )}
        {item.doi && (
          <p className="muted">
            DOI: <a href={`https://doi.org/${item.doi}`}>{item.doi}</a>
          </p>
        )}
      </div>

      <div className="card">
        <h2>PDFs</h2>
        {canEdit && (
          <label
            className="secondary"
            style={{ padding: "0.55rem 0.7rem", borderRadius: 8, display: "inline-block" }}
          >
            Upload PDF
            <input type="file" accept="application/pdf" onChange={onUpload} style={{ display: "none" }} />
          </label>
        )}
        {item.attachments.length === 0 && <p className="muted">No PDFs attached.</p>}
        {item.attachments.map((a) => (
          <div className="item-row" key={a.id}>
            <div className="grow">
              {a.filename} <span className="muted">({Math.round(a.size / 1024)} KB)</span>
            </div>
            <div className="row">
              <button className="secondary" type="button" onClick={() => view(a.id)}>
                View
              </button>
              <a href={api.attachmentDownloadUrl(a.id)}>
                <button className="secondary" type="button">
                  Download
                </button>
              </a>
            </div>
          </div>
        ))}
      </div>

      {viewUrl && (
        <div className="card">
          <PdfViewer url={viewUrl} />
        </div>
      )}

      <div className="card">
        <h2>Notes</h2>
        {notes.length === 0 && <p className="muted">No notes yet.</p>}
        {notes.map((n) => (
          <div className="item-row" key={n.id}>
            <span className="grow note-body">{n.body}</span>
            {canEdit && (
              <button
                className="secondary"
                type="button"
                onClick={() => api.deleteNote(n.id).then(reload)}
              >
                Delete
              </button>
            )}
          </div>
        ))}
        {canEdit && (
          <form className="row" style={{ marginTop: "0.5rem" }} onSubmit={addNote}>
            <input
              className="grow"
              placeholder="Add a note…"
              value={noteBody}
              onChange={(e) => setNoteBody(e.target.value)}
            />
            <button type="submit">Add</button>
          </form>
        )}
      </div>

      <div className="card">
        <h2>History</h2>
        {history.map((ev) => (
          <div className="event row" key={ev.id}>
            <span className="grow">
              <strong>{ev.operation}</strong> · {ev.summary}{" "}
              <span className="muted">{new Date(ev.occurred_at).toLocaleString()}</span>
            </span>
            {canEdit && ev.entity_type === "item" && ev.operation !== "delete" && (
              <button className="secondary" type="button" onClick={() => restore(ev.id)}>
                Restore
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
