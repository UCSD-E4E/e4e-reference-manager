import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Group, Library } from "../types";

export default function Libraries() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState(""); // "" = personal, else group id
  const [err, setErr] = useState("");

  const load = () => api.listLibraries().then(setLibs).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    api.listGroups().then(setGroups).catch(() => setGroups([]));
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      if (owner) await api.createGroupLibrary(name.trim(), owner);
      else await api.createLibrary(name.trim());
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
        <select value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Personal</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button type="submit">Create</button>
      </form>

      {libs.length === 0 && <p className="muted">No projects yet. Create one above.</p>}
      {libs.map((l) => (
        <div className="card" key={l.id}>
          <Link to={`/libraries/${l.id}`}>
            <strong>{l.name}</strong>
          </Link>{" "}
          {l.owner_group_id && <span className="tag">group</span>}
          {l.my_access && <span className="muted"> · {l.my_access}</span>}
          {l.description && <div className="muted">{l.description}</div>}
        </div>
      ))}
    </div>
  );
}
