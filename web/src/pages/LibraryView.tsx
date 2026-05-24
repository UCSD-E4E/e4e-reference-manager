import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { ImportResult, Item, Library } from "../types";

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

  // manual add form
  const [key, setKey] = useState("");
  const [type, setType] = useState("article-journal");
  const [title, setTitle] = useState("");
  const [year, setYear] = useState("");
  const [authors, setAuthors] = useState("");

  const loadItems = (query = "") =>
    api
      .listItems(libId, query)
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    api.getLibrary(libId).then(setLib).catch((e) => setErr(String(e)));
    loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libId]);

  const onImport = async (e: FormEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    setErr("");
    try {
      const result = await api.importBib(libId, file);
      setImp(result);
      loadItems(q);
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
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div>
      <p className="muted">
        <Link to="/">← Projects</Link>
      </p>
      <h1>{lib?.name ?? "Project"}</h1>
      {err && <p className="error">{err}</p>}

      <div className="card">
        <h2>Library tools</h2>
        <div className="row">
          <label className="secondary" style={{ padding: "0.55rem 0.7rem", borderRadius: 8 }}>
            Import .bib
            <input type="file" accept=".bib" onChange={onImport} style={{ display: "none" }} />
          </label>
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
      </div>

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
    </div>
  );
}
