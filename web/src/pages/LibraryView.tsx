import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { AuditEvent, Group, ImportResult, Item, Library, Share } from "../types";

const CSL_TYPES = [
  "article-journal",
  "paper-conference",
  "book",
  "chapter",
  "report",
  "thesis",
  "webpage",
  "document",
];

export default function LibraryView() {
  const { libId = "" } = useParams();
  const [lib, setLib] = useState<Library | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [imp, setImp] = useState<ImportResult | null>(null);
  const [activity, setActivity] = useState<AuditEvent[]>([]);
  const [shares, setShares] = useState<Share[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [shareGroup, setShareGroup] = useState("");
  const [shareLevel, setShareLevel] = useState("view");
  const [ingestQuery, setIngestQuery] = useState("");
  const [ingestMsg, setIngestMsg] = useState("");

  // manual add form
  const [key, setKey] = useState("");
  const [type, setType] = useState("article-journal");
  const [title, setTitle] = useState("");
  const [year, setYear] = useState("");
  const [authors, setAuthors] = useState("");

  const canEdit = lib?.my_access === "edit" || lib?.my_access === "manage";
  const canManage = lib?.my_access === "manage";

  const loadItems = (query = "") =>
    api
      .listItems(libId, query)
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setErr(String(e)));

  const loadMeta = async () => {
    try {
      const l = await api.getLibrary(libId);
      setLib(l);
      setActivity(await api.libraryActivity(libId));
      if (l.my_access === "manage") {
        setShares(await api.listShares(libId));
        setGroups(await api.listGroups());
      }
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    loadMeta();
    loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libId]);

  const onImport = async (e: FormEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    setErr("");
    try {
      setImp(await api.importBib(libId, file));
      loadItems(q);
      loadMeta();
    } catch (e) {
      setErr(String(e));
    }
    e.currentTarget.value = "";
  };

  const onAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    const csl: Record<string, unknown> = { type, title: title.trim() };
    if (authors.trim()) {
      csl.author = authors.split(";").map((a) => {
        const [family, given] = a.split(",").map((s) => s.trim());
        return given ? { family, given } : { family: family ?? a.trim() };
      });
    }
    if (year.trim()) csl.issued = { "date-parts": [[Number(year.trim())]] };
    try {
      await api.createItem(libId, key.trim(), type, csl);
      setKey("");
      setTitle("");
      setYear("");
      setAuthors("");
      loadItems(q);
      loadMeta();
    } catch (e) {
      setErr(String(e));
    }
  };

  const doIngest = async (e: FormEvent) => {
    e.preventDefault();
    if (!ingestQuery.trim()) return;
    setIngestMsg("Looking up…");
    try {
      const { results } = await api.ingest(libId, ingestQuery.trim());
      const created = results.filter((r) => r.status === "created").length;
      const dup = results.filter((r) => r.status === "duplicate").length;
      setIngestMsg(`Imported ${created}${dup ? `, ${dup} already in library` : ""}.`);
      setIngestQuery("");
      loadItems(q);
      loadMeta();
    } catch (e) {
      setIngestMsg("");
      setErr(String(e));
    }
  };

  const onFromPdf = async (e: FormEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    setIngestMsg("Extracting metadata from PDF…");
    try {
      await api.createItemFromPdf(libId, file);
      setIngestMsg("Added reference from PDF.");
      loadItems(q);
      loadMeta();
    } catch (e) {
      setIngestMsg("");
      setErr(String(e));
    }
    e.currentTarget.value = "";
  };

  const addShare = async (e: FormEvent) => {
    e.preventDefault();
    if (!shareGroup) return;
    try {
      await api.upsertShare(libId, shareGroup, shareLevel);
      setShares(await api.listShares(libId));
    } catch (e) {
      setErr(String(e));
    }
  };

  const groupName = (id: string) => groups.find((g) => g.id === id)?.name ?? id.slice(0, 8);

  return (
    <div>
      <p className="muted">
        <Link to="/">← Projects</Link>
      </p>
      <h1>
        {lib?.name ?? "Project"}{" "}
        {lib?.my_access && <span className="tag">{lib.my_access}</span>}
      </h1>
      {err && <p className="error">{err}</p>}

      <div className="card">
        <h2>Library tools</h2>
        <div className="row">
          {canEdit && (
            <label className="secondary" style={{ padding: "0.55rem 0.7rem", borderRadius: 8 }}>
              Import .bib
              <input type="file" accept=".bib" onChange={onImport} style={{ display: "none" }} />
            </label>
          )}
          <a href={api.exportLibraryUrl(libId)}>
            <button className="secondary" type="button">
              Export .bib
            </button>
          </a>
        </div>
        {imp && (
          <p className={imp.key_collisions.length ? "warn" : "muted"} style={{ marginTop: "0.5rem" }}>
            Imported {imp.imported} entr{imp.imported === 1 ? "y" : "ies"} from {imp.filename}.
            {imp.key_collisions.length > 0 &&
              ` Duplicate citation keys flagged (kept as-is): ${imp.key_collisions.join(", ")}.`}
          </p>
        )}
        {canEdit && (
          <>
            <form className="row" style={{ marginTop: "0.5rem" }} onSubmit={doIngest}>
              <input
                className="grow"
                placeholder="Import by DOI, arXiv ID, PMID, ISBN, or URL…"
                value={ingestQuery}
                onChange={(e) => setIngestQuery(e.target.value)}
              />
              <button type="submit">Fetch</button>
              <label
                className="secondary"
                style={{ padding: "0.55rem 0.7rem", borderRadius: 8, cursor: "pointer" }}
              >
                Add from PDF
                <input type="file" accept="application/pdf" onChange={onFromPdf} style={{ display: "none" }} />
              </label>
            </form>
            {ingestMsg && <p className="muted">{ingestMsg}</p>}
          </>
        )}
      </div>

      {canEdit && (
        <form className="card" onSubmit={onAdd}>
          <h2>Add a reference</h2>
          <div className="row">
            <input className="grow" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <input placeholder="Citation key" value={key} onChange={(e) => setKey(e.target.value)} />
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {CSL_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input placeholder="Year" value={year} onChange={(e) => setYear(e.target.value)} style={{ width: 90 }} />
          </div>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <input
              className="grow"
              placeholder="Authors (Family, Given; Family, Given)"
              value={authors}
              onChange={(e) => setAuthors(e.target.value)}
            />
            <button type="submit">Add</button>
          </div>
        </form>
      )}

      <div className="card">
        <div className="row">
          <input
            className="grow"
            placeholder="Search titles…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              loadItems(e.target.value);
            }}
          />
          <span className="muted">{total} references</span>
        </div>
        <div style={{ marginTop: "0.5rem" }}>
          {items.map((it) => (
            <div className="item-row" key={it.id}>
              <div className="grow">
                <Link to={`/items/${it.id}`}>
                  <strong>{it.title || "(untitled)"}</strong>
                </Link>
                <div className="muted">
                  {it.citation_key} · {it.type}
                  {it.year ? ` · ${it.year}` : ""}
                  {it.attachments.length ? ` · 📎 ${it.attachments.length}` : ""}
                </div>
              </div>
            </div>
          ))}
          {items.length === 0 && <p className="muted">No references yet.</p>}
        </div>
      </div>

      {canManage && (
        <div className="card">
          <h2>Sharing</h2>
          {shares.length === 0 && <p className="muted">Not shared with any group.</p>}
          {shares.map((s) => (
            <div className="item-row" key={s.id}>
              <span className="grow">
                {groupName(s.group_id)} <span className="tag">{s.access_level}</span>
              </span>
              <button
                className="secondary"
                type="button"
                onClick={() => api.deleteShare(libId, s.group_id).then(() => api.listShares(libId).then(setShares))}
              >
                Remove
              </button>
            </div>
          ))}
          <form className="row" style={{ marginTop: "0.5rem" }} onSubmit={addShare}>
            <select className="grow" value={shareGroup} onChange={(e) => setShareGroup(e.target.value)}>
              <option value="">Select a group…</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
            <select value={shareLevel} onChange={(e) => setShareLevel(e.target.value)}>
              <option value="view">view</option>
              <option value="edit">edit</option>
              <option value="manage">manage</option>
            </select>
            <button type="submit">Share</button>
          </form>
        </div>
      )}

      <div className="card">
        <h2>Activity</h2>
        {activity.length === 0 && <p className="muted">No activity yet.</p>}
        {activity.map((ev) => (
          <div className="event" key={ev.id}>
            <strong>{ev.operation}</strong> · {ev.summary}{" "}
            <span className="muted">{new Date(ev.occurred_at).toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
