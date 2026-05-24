import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Library } from "../types";

export default function Libraries() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [name, setName] = useState("");
  const [err, setErr] = useState("");

  const load = () => api.listLibraries().then(setLibs).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      await api.createLibrary(name.trim());
      setName("");
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div>
      <h1>Projects</h1>
      {err && <p className="error">{err}</p>}

      <form className="card row" onSubmit={create}>
        <input
          className="grow"
          placeholder="New project name…"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit">Create</button>
      </form>

      {libs.length === 0 && <p className="muted">No projects yet. Create one above.</p>}
      {libs.map((l) => (
        <div className="card" key={l.id}>
          <Link to={`/libraries/${l.id}`}>
            <strong>{l.name}</strong>
          </Link>
          {l.description && <div className="muted">{l.description}</div>}
        </div>
      ))}
    </div>
  );
}
