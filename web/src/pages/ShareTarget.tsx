import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { Library } from "../types";

/** Handles the PWA Web Share Target: a URL/DOI shared from the phone lands here. */
export default function ShareTarget() {
  const [params] = useSearchParams();
  const shared = (params.get("url") || params.get("text") || params.get("title") || "").trim();
  const [libs, setLibs] = useState<Library[]>([]);
  const [libId, setLibId] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    api
      .listLibraries()
      .then((all) => {
        const editable = all.filter((l) => l.my_access === "edit" || l.my_access === "manage");
        setLibs(editable);
        if (editable[0]) setLibId(editable[0].id);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const ingest = async () => {
    if (!libId || !shared) return;
    setMsg("Importing…");
    try {
      const { results } = await api.ingest(libId, shared);
      const created = results.filter((r) => r.status === "created").length;
      const dup = results.length - created;
      setMsg(`Imported ${created}${dup ? `, ${dup} already present` : ""}.`);
      setDone(libId);
    } catch (e) {
      setMsg("");
      setErr(String(e));
    }
  };

  return (
    <div>
      <h1>Save to e4e References</h1>
      {err && <p className="error">{err}</p>}
      {!shared && <p className="warn">Nothing was shared.</p>}
      {shared && (
        <div className="card">
          <p className="muted">Shared:</p>
          <p style={{ wordBreak: "break-all" }}>{shared}</p>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <select className="grow" value={libId} onChange={(e) => setLibId(e.target.value)}>
              {libs.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
            <button type="button" onClick={ingest} disabled={!libId}>
              Import
            </button>
          </div>
          {libs.length === 0 && <p className="muted">You have no projects you can add to.</p>}
          {msg && <p className="muted">{msg}</p>}
          {done && (
            <p>
              <Link to={`/libraries/${done}`}>Open project →</Link>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
