import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { Library, TrashedItem } from "../types";

const fmt = (iso: string) => new Date(iso).toLocaleDateString();

export default function Trash() {
  const { libId = "" } = useParams();
  const [lib, setLib] = useState<Library | null>(null);
  const [trash, setTrash] = useState<TrashedItem[]>([]);
  const [err, setErr] = useState("");
  const canEdit = lib?.my_access === "edit" || lib?.my_access === "manage";

  const load = () => api.listTrash(libId).then(setTrash).catch((e) => setErr(String(e)));
  useEffect(() => {
    api.getLibrary(libId).then(setLib).catch((e) => setErr(String(e)));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libId]);

  const act = async (fn: () => Promise<unknown>) => {
    setErr("");
    try {
      await fn();
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const deleteForever = (t: TrashedItem) => {
    if (!confirm(`Permanently delete “${t.item.title || t.item.citation_key}”? This can't be undone.`))
      return;
    act(() => api.deleteForever(libId, t.item.id));
  };

  const emptyTrash = () => {
    if (!confirm(`Permanently delete all ${trash.length} references in the trash? This can't be undone.`))
      return;
    act(() => api.emptyTrash(libId));
  };

  return (
    <div>
      <p className="muted">
        <Link to={`/libraries/${libId}`}>← Back to {lib?.name ?? "project"}</Link>
      </p>
      <div className="row">
        <h1 className="grow">Trash</h1>
        {canEdit && trash.length > 0 && (
          <button className="danger" type="button" onClick={emptyTrash}>
            Empty trash
          </button>
        )}
      </div>
      <p className="muted">
        Deleted references stay here for 30 days, with their notes, PDFs and collections, and
        can be restored until then.
      </p>
      {err && <p className="error">{err}</p>}
      {trash.length === 0 && <p className="muted">The trash is empty.</p>}
      {trash.length > 0 && (
        <div className="card">
          {trash.map((t) => (
            <div className="item-row" key={t.item.id}>
              <span className="grow">
                <strong>{t.item.title || t.item.citation_key}</strong>{" "}
                <span className="muted">{t.item.year ?? ""}</span>
                <br />
                <span className="muted">
                  Deleted {fmt(t.deleted_at)}
                  {t.deleted_by && ` by ${t.deleted_by.name || t.deleted_by.email}`} · deleted
                  for good on {fmt(t.purge_at)}
                </span>
              </span>
              {canEdit && (
                <>
                  <button
                    type="button"
                    onClick={() => act(() => api.restoreFromTrash(libId, t.item.id))}
                  >
                    Restore
                  </button>
                  <button className="secondary" type="button" onClick={() => deleteForever(t)}>
                    Delete forever
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
