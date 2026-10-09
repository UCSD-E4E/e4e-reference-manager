import { FormEvent, useState } from "react";
import { api } from "../api";
import { CSL_TYPES } from "../csl";
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

/** Edit an item's metadata. Fields the form doesn't show are carried over untouched,
 * and authors/year are only rewritten if changed, so saving never loses detail. */
export default function ItemEditor({
  item,
  onSaved,
  onCancel,
}: {
  item: Item;
  onSaved: (item: Item) => void;
  onCancel: () => void;
}) {
  const csl = item.csl_json as Csl;
  const initialAuthors = namesToText(csl.author);
  const [key, setKey] = useState(item.citation_key);
  const [type, setType] = useState(item.type);
  const [title, setTitle] = useState(str(csl.title));
  const [authors, setAuthors] = useState(initialAuthors);
  const [year, setYear] = useState(item.year ? String(item.year) : "");
  const [abstract, setAbstract] = useState(str(csl.abstract));
  const [fields, setFields] = useState<Record<string, string>>(
    Object.fromEntries(FIELDS.map(([k]) => [k, str(csl[k])])),
  );
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

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
    if (authors !== initialAuthors) {
      const names = textToNames(authors);
      if (names.length) next.author = names;
      else delete next.author;
    }
    if (y !== (item.year ? String(item.year) : "")) {
      if (y) next.issued = { "date-parts": [[Number(y)]] };
      else delete next.issued;
    }

    setSaving(true);
    setErr("");
    try {
      onSaved(await api.updateItem(item.id, item.version, next, { citation_key: key.trim(), type }));
    } catch (e) {
      const msg = String(e);
      setErr(
        msg.includes("409")
          ? "Someone else changed this reference while you were editing. Copy anything you " +
              "need, then cancel and reload to see their version."
          : msg,
      );
      setSaving(false);
    }
  };

  const types = CSL_TYPES.includes(item.type) ? CSL_TYPES : [item.type, ...CSL_TYPES];

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
      {err && <p className="error">{err}</p>}
      <div className="row">
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="secondary" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
