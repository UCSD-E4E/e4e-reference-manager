import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import ValidationBadge from "../components/ValidationBadge";
import type {
  AuditEvent,
  AutoGroup,
  AutoGroupGenerateSource,
  Collection,
  Group,
  ImportResult,
  Item,
  Library,
  SearchMode,
  Share,
} from "../types";

const GEN_SOURCES: { value: AutoGroupGenerateSource; label: string }[] = [
  { value: "year", label: "By year" },
  { value: "type", label: "By CSL type" },
  { value: "author", label: "By author" },
  { value: "journal", label: "By journal" },
  { value: "ml_tags", label: "From ML-suggested tags" },
  { value: "manual_tags", label: "From manual tags" },
  { value: "all_tags", label: "From all tags" },
];

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
  const [mode, setMode] = useState<SearchMode>("keyword");
  const [reindexMsg, setReindexMsg] = useState("");
  const [dedupeMsg, setDedupeMsg] = useState("");
  const [err, setErr] = useState("");
  const [imp, setImp] = useState<ImportResult | null>(null);
  const [activity, setActivity] = useState<AuditEvent[]>([]);
  const [shares, setShares] = useState<Share[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [shareGroup, setShareGroup] = useState("");
  const [shareLevel, setShareLevel] = useState("view");
  const [ingestQuery, setIngestQuery] = useState("");
  const [ingestMsg, setIngestMsg] = useState("");
  const [collections, setCollections] = useState<Collection[]>([]);
  const [newColl, setNewColl] = useState("");
  const [autoGroups, setAutoGroups] = useState<AutoGroup[]>([]);
  const [genSource, setGenSource] = useState<AutoGroupGenerateSource>("year");
  const [genMsg, setGenMsg] = useState("");
  const [searchAgName, setSearchAgName] = useState("");
  const [validateMsg, setValidateMsg] = useState("");

  // manual add form
  const [key, setKey] = useState("");
  const [type, setType] = useState("article-journal");
  const [title, setTitle] = useState("");
  const [year, setYear] = useState("");
  const [authors, setAuthors] = useState("");

  const canEdit = lib?.my_access === "edit" || lib?.my_access === "manage";
  const canManage = lib?.my_access === "manage";
  const navigate = useNavigate();

  const doDeleteProject = async () => {
    if (!lib) return;
    // Typing the name (not just OK) guards against deleting the wrong project.
    const typed = window.prompt(
      `Delete “${lib.name}” and everything in it (items, notes, collections, ` +
        `annotations)? This cannot be undone.\n\nType the project name to confirm:`,
    );
    if (typed === null) return;
    if (typed.trim() !== lib.name) {
      setErr("Project name didn't match — nothing was deleted.");
      return;
    }
    try {
      await api.deleteLibrary(lib.id);
      navigate("/");
    } catch (e) {
      setErr(String(e));
    }
  };

  const loadItems = (query = "") =>
    api
      .listItems(libId, query)
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setErr(String(e)));

  // Browse (empty query) and keyword mode use the FTS list endpoint; semantic/hybrid use
  // the dedicated /search endpoint (pgvector + RRF).
  const runSearch = (query: string, m: SearchMode = mode) => {
    if (!query.trim() || m === "keyword") return loadItems(query);
    return api
      .searchItems(libId, query, m)
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setErr(String(e)));
  };

  const doValidate = async () => {
    setValidateMsg("Validating sources…");
    try {
      const s = await api.validateLibrary(libId);
      setValidateMsg(
        `Checked ${s.checked}: ${s.verified} verified, ${s.metadata_mismatch} mismatch, ${s.not_found} not found, ${s.unverifiable} unverifiable.`,
      );
      loadItems(q); // refresh badges
    } catch (e) {
      setValidateMsg("");
      setErr(String(e));
    }
  };

  const doDedupe = async () => {
    setErr("");
    try {
      const preview = await api.dedupeLibrary(libId, true);
      if (preview.merged === 0) {
        setDedupeMsg("No duplicates found.");
        return;
      }
      const ok = window.confirm(
        `Merge ${preview.merged} duplicate item${preview.merged === 1 ? "" : "s"} into ` +
          `${preview.groups.length}? The oldest copy of each paper is kept; PDFs, notes, ` +
          `tags and collections from the copies move onto it.`,
      );
      if (!ok) return;
      const r = await api.dedupeLibrary(libId, false);
      setDedupeMsg(`Merged ${r.merged} duplicate item${r.merged === 1 ? "" : "s"}.`);
      loadItems(q);
      loadMeta();
    } catch (e) {
      setErr(String(e));
    }
  };

  const doReindex = async () => {
    setReindexMsg("Reindexing embeddings…");
    try {
      const r = await api.reindexLibrary(libId);
      setReindexMsg(`Embedded ${r.embedded} of ${r.items} references.`);
    } catch (e) {
      setReindexMsg("");
      setErr(String(e));
    }
  };

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

  const loadCollections = () =>
    api.listCollections(libId).then(setCollections).catch((e) => setErr(String(e)));

  const loadAutoGroups = () =>
    api.listAutoGroups(libId).then(setAutoGroups).catch((e) => setErr(String(e)));

  useEffect(() => {
    loadMeta();
    loadItems();
    loadCollections();
    loadAutoGroups();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libId]);

  const doGenerateAutoGroups = async () => {
    setGenMsg("Generating…");
    try {
      if (genSource === "ml_tags") setGenMsg("Asking the model to tag untagged items…");
      const r = await api.generateAutoGroups(libId, genSource);
      let msg = `Created ${r.created} new auto-group${r.created === 1 ? "" : "s"}.`;
      if (r.tagged !== undefined && r.remaining !== undefined) {
        msg += ` ML-tagged ${r.tagged} item${r.tagged === 1 ? "" : "s"}`;
        if (r.remaining > 0) {
          msg +=
            r.tagged > 0
              ? `; ${r.remaining} still untagged — run again to continue.`
              : `; ${r.remaining} untagged and the model gave no tags (is Ollama running?).`;
        } else msg += ".";
      }
      setGenMsg(msg);
      loadAutoGroups();
    } catch (e) {
      setGenMsg("");
      setErr(String(e));
    }
  };

  const createSearchAutoGroup = async (e: FormEvent) => {
    e.preventDefault();
    if (!searchAgName.trim() || !q.trim()) return;
    try {
      await api.createAutoGroup(libId, {
        name: searchAgName.trim(),
        kind: "search",
        params: { q: q.trim(), mode: "keyword" },
      });
      setSearchAgName("");
      loadAutoGroups();
    } catch (e) {
      setErr(String(e));
    }
  };

  const addCollection = async (e: FormEvent) => {
    e.preventDefault();
    if (!newColl.trim()) return;
    try {
      await api.createCollection(libId, newColl.trim());
      setNewColl("");
      loadCollections();
    } catch (e) {
      setErr(String(e));
    }
  };

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
          {canEdit && (
            <button className="secondary" type="button" onClick={doReindex}>
              Reindex search
            </button>
          )}
          {canEdit && (
            <button className="secondary" type="button" onClick={doDedupe}>
              Merge duplicates
            </button>
          )}
          {canEdit && (
            <button
              className="secondary"
              type="button"
              onClick={doValidate}
              title="Verify each reference against Crossref/arXiv to flag AI-hallucinated sources"
            >
              Validate sources
            </button>
          )}
        </div>
        {reindexMsg && <p className="muted" style={{ marginTop: "0.5rem" }}>{reindexMsg}</p>}
        {dedupeMsg && <p className="muted" style={{ marginTop: "0.5rem" }}>{dedupeMsg}</p>}
        {validateMsg && <p className="muted" style={{ marginTop: "0.5rem" }}>{validateMsg}</p>}
        {imp && (
          <p className={imp.key_collisions.length ? "warn" : "muted"} style={{ marginTop: "0.5rem" }}>
            Imported {imp.imported} entr{imp.imported === 1 ? "y" : "ies"} from {imp.filename}.
            {imp.duplicates.length > 0 &&
              ` Skipped ${imp.duplicates.length} already in the project: ${imp.duplicates
                .map((d) => d.citation_key)
                .join(", ")}.`}
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
            placeholder={mode === "keyword" ? "Search title, abstract, authors, PDF text…" : "Search by meaning…"}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              runSearch(e.target.value);
            }}
          />
          <select
            value={mode}
            onChange={(e) => {
              const m = e.target.value as SearchMode;
              setMode(m);
              runSearch(q, m);
            }}
            title="keyword = full-text · semantic = meaning · hybrid = both"
          >
            <option value="keyword">keyword</option>
            <option value="semantic">semantic</option>
            <option value="hybrid">hybrid</option>
          </select>
          <span className="muted">{total} references</span>
        </div>
        <div style={{ marginTop: "0.5rem" }}>
          {items.map((it) => (
            <div className="item-row" key={it.id}>
              <div className="grow">
                <Link to={`/items/${it.id}`}>
                  <strong>{it.title || "(untitled)"}</strong>
                </Link>
                <ValidationBadge v={it.validation} />
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

      <div className="card">
        <h2>Collections</h2>
        {collections.length === 0 && <p className="muted">No collections yet.</p>}
        {collections.map((c) => (
          <div className="item-row" key={c.id}>
            <span className="grow">{c.name}</span>
            <div className="row">
              <a href={api.exportCollectionUrl(c.id)}>
                <button className="secondary" type="button">
                  Export .bib
                </button>
              </a>
              {canEdit && (
                <button
                  className="secondary"
                  type="button"
                  onClick={() => api.deleteCollection(c.id).then(loadCollections)}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
        {canEdit && (
          <form className="row" style={{ marginTop: "0.5rem" }} onSubmit={addCollection}>
            <input
              className="grow"
              placeholder="New collection name…"
              value={newColl}
              onChange={(e) => setNewColl(e.target.value)}
            />
            <button type="submit">Create</button>
          </form>
        )}
      </div>

      <div className="card">
        <h2>Auto-groups</h2>
        <p className="muted">
          Live, rule-driven groups (JabRef-style). Membership is recomputed on demand —
          new items matching the rule appear automatically.
        </p>
        {autoGroups.length === 0 && <p className="muted">None yet.</p>}
        {autoGroups.map((g) => (
          <div className="item-row" key={g.id}>
            <span className="grow">
              <strong>{g.name}</strong>{" "}
              <span className="tag">{g.kind}</span>{" "}
              <span className="muted">{g.count} item{g.count === 1 ? "" : "s"}</span>
            </span>
            <div className="row">
              <a href={api.exportAutoGroupUrl(g.id)}>
                <button className="secondary" type="button">Export .bib</button>
              </a>
              {canEdit && (
                <button
                  className="secondary"
                  type="button"
                  onClick={() => api.deleteAutoGroup(g.id).then(loadAutoGroups)}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
        {canEdit && (
          <>
            <div className="row" style={{ marginTop: "0.5rem" }}>
              <select
                value={genSource}
                onChange={(e) => setGenSource(e.target.value as AutoGroupGenerateSource)}
              >
                {GEN_SOURCES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
              <button type="button" onClick={doGenerateAutoGroups}>
                Generate
              </button>
              {genMsg && <span className="muted">{genMsg}</span>}
            </div>
            <form
              className="row"
              style={{ marginTop: "0.5rem" }}
              onSubmit={createSearchAutoGroup}
            >
              <input
                className="grow"
                placeholder={
                  q.trim()
                    ? `Save current search “${q.trim()}” as an auto-group, named…`
                    : "Type a search above first, then name the auto-group here…"
                }
                value={searchAgName}
                onChange={(e) => setSearchAgName(e.target.value)}
                disabled={!q.trim()}
              />
              <button type="submit" disabled={!q.trim() || !searchAgName.trim()}>
                Save search
              </button>
            </form>
          </>
        )}
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

      {canManage && (
        <div className="card">
          <h2>Delete project</h2>
          <p className="muted">
            Permanently deletes this project and all of its items, notes, collections and
            annotations for everyone it's shared with.
          </p>
          <button className="danger" type="button" onClick={doDeleteProject}>
            Delete project
          </button>
        </div>
      )}
    </div>
  );
}
