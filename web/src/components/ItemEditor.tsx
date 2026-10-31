import { FormEvent, useEffect, useState } from "react";
import { api, holdLoginRedirect } from "../api";
import { CSL_TYPES, THESIS_GENRES } from "../csl";
import type { Item } from "../types";

type Csl = Record<string, unknown>;
type Name = { family?: string; given?: string; literal?: string };

const namesToText = (names: unknown) =>
  ((names as Name[] | undefined) ?? [])
    .map((n) => n.literal ?? [n.family, n.given].filter(Boolean).join(", "))
    .join("\n");

const textToNames = (text: string): Name[] =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [family, ...given] = l.split(",");
      return given.length
        ? { family: family.trim(), given: given.join(",").trim() }
        : { literal: l }; // no comma: an organisation, or a name we shouldn't guess at
    });

const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : "");

// Simple text fields edited as-is; empty means "remove the field".
const FIELDS: [key: string, label: string][] = [
  ["container-title", "Journal / venue"],
  ["volume", "Volume"],
  ["issue", "Issue"],
  ["page", "Pages"],
  ["publisher", "Publisher"],
  ["DOI", "DOI"],
  ["URL", "URL"],
];

const CONFLICT =
  "This reference changed while you were editing (someone else's edit, or a restore or " +
  "metadata fill on this page). Saving would overwrite that, so it's blocked: copy " +
  "anything you need, then cancel and edit again.";

/** Edit an item's metadata. Fields the form doesn't show are carried over untouched,
 * and authors/year are only rewritten if changed, so saving never loses detail.
 *
 * The form edits the item as it was when editing began (`base`): the page may reload
 * `item` underneath (after a restore, PDF upload, metadata fill…), and saving the form
 * against that newer version would silently overwrite it. Saving sends base's version,
 * so the API answers 409 instead, and a newer `item` prop blocks saving up front. */
export default function ItemEditor({
  item,
  onSaved,
  onCancel,
}: {
  item: Item;
  onSaved: (item: Item) => void;
  onCancel: () => void;
}) {
  const [base] = useState(item);
  const csl = base.csl_json as Csl;
  const initialAuthors = namesToText(csl.author);
  const initialYear = base.year ? String(base.year) : "";
  const [key, setKey] = useState(base.citation_key);
  const [type, setType] = useState(base.type);
  const [genre, setGenre] = useState(str(csl.genre));
  const [title, setTitle] = useState(str(csl.title));
  const [authors, setAuthors] = useState(initialAuthors);
  const [year, setYear] = useState(initialYear);
  const [abstract, setAbstract] = useState(str(csl.abstract));
  const [fields, setFields] = useState<Record<string, string>>(
    Object.fromEntries(FIELDS.map(([k]) => [k, str(csl[k])])),
  );
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const stale = item.id === base.id && item.version !== base.version;

  // An expired session must not navigate away from unsaved edits (see api.ts).
  useEffect(() => holdLoginRedirect(), []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!key.trim()) {
      setErr("The citation key can't be empty.");
      return;
    }
    const y = year.trim();
    if (y && !/^\d{1,4}$/.test(y)) {
      setErr("Year must be a number.");
      return;
    }
    const next: Csl = { ...csl, type, title: title.trim() };
    const set = (k: string, v: string) => {
      if (v.trim()) next[k] = v.trim();
      else delete next[k];
    };
    for (const [k] of FIELDS) set(k, fields[k]);
    set("abstract", abstract);
    if (type === "thesis") set("genre", genre);
    if (authors !== initialAuthors) {
      const names = textToNames(authors);
      if (names.length) next.author = names;
      else delete next.author;
    }
    if (y !== initialYear) {
      if (y) next.issued = { "date-parts": [[Number(y)]] };
      else delete next.issued;
    }

    setSaving(true);
    setErr("");
    try {
      onSaved(await api.updateItem(base.id, base.version, next, { citation_key: key.trim(), type }));
    } catch (e) {
      const msg = String(e);
      setErr(msg.includes("409") ? CONFLICT : msg);
      setSaving(false);
    }
  };

  const types = CSL_TYPES.includes(base.type) ? CSL_TYPES : [base.type, ...CSL_TYPES];
  const initialGenre = str(csl.genre);
  const genres =
    !initialGenre || THESIS_GENRES.includes(initialGenre) ? THESIS_GENRES : [initialGenre, ...THESIS_GENRES];

  return (
    <form className="card editor" onSubmit={save}>
      <h2>Edit reference</h2>
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Authors <span className="muted">(one per line: Family, Given)</span>
        <textarea rows={4} value={authors} onChange={(e) => setAuthors(e.target.value)} />
      </label>
      <div className="editor-grid">
        <label>
          Citation key
          <input value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        <label>
          Type
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {type === "thesis" && (
          <label>
            Thesis kind
            <select value={genre} onChange={(e) => setGenre(e.target.value)}>
              <option value="">(not given)</option>
              {genres.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Year
          <input inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value)} />
        </label>
        {FIELDS.map(([k, label]) => (
          <label key={k}>
            {label}
            <input
              value={fields[k]}
              onChange={(e) => setFields((f) => ({ ...f, [k]: e.target.value }))}
            />
          </label>
        ))}
      </div>
      <label>
        Abstract
        <textarea rows={5} value={abstract} onChange={(e) => setAbstract(e.target.value)} />
      </label>
      {stale ? <p className="error">{CONFLICT}</p> : err && <p className="error">{err}</p>}
      <div className="row">
        <button type="submit" disabled={saving || stale}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="secondary" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
