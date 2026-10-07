import { useState } from "react";
import { api } from "../api";
import type { ImportResult, PastePreviewEntry } from "../types";
import ValidationBadge from "./ValidationBadge";

/** Pre-ticked: new entries that check out, or that no registrar indexes (reports, theses).
 * Not found / title mismatch — the AI-hallucination signatures — start unticked. */
const addByDefault = (e: PastePreviewEntry) =>
  !e.duplicate && (e.validation?.status === "verified" || e.validation?.status === "unverifiable");

export default function PasteBibtex({
  libId,
  onAdded,
  onClose,
}: {
  libId: string;
  onAdded: (r: ImportResult) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [entries, setEntries] = useState<PastePreviewEntry[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const check = async () => {
    setErr("");
    setBusy("Parsing and checking each reference against Crossref / arXiv…");
    try {
      const r = await api.previewPaste(libId, text);
      setEntries(r.entries);
      setSelected(new Set(r.entries.filter(addByDefault).map((e) => e.index)));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const add = async () => {
    setErr("");
    setBusy("Adding…");
    try {
      onAdded(await api.importPaste(libId, text, [...selected].sort((a, b) => a - b)));
    } catch (e) {
      setErr(String(e));
      setBusy("");
    }
  };

  const toggle = (i: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  return (
    <div className="card">
      <div className="row">
        <h2 className="grow">Paste BibTeX</h2>
        <button className="secondary" type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
      {entries === null ? (
        <>
          <textarea
            rows={10}
            placeholder="@article{key, title={…}, author={…}, year={…}, doi={…}}"
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ fontFamily: "monospace" }}
          />
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <button type="button" onClick={check} disabled={!text.trim() || !!busy}>
              Check references
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="muted">
            Ticked entries will be added. References that weren't found, or whose DOI points to a
            different paper, start unticked — check them before adding.
          </p>
          <div className="paste-preview">
            {entries.map((e) => (
              <label key={e.index} className="paste-row">
                <input
                  type="checkbox"
                  checked={selected.has(e.index)}
                  disabled={!!e.duplicate}
                  onChange={() => toggle(e.index)}
                />
                <span className="grow">
                  <strong>{e.title || e.citation_key}</strong>
                  {e.year ? ` (${e.year})` : ""}
                  <span className="muted"> · {e.citation_key}</span>
                  {e.key_collision && (
                    <span className="warn-inline"> · key already used by another paper</span>
                  )}
                </span>
                <span className="paste-status">
                  {e.duplicate ? (
                    <span className="muted">
                      {e.duplicate.item_id
                        ? "already in project"
                        : `repeat of entry ${(e.duplicate.entry_index ?? 0) + 1}`}
                    </span>
                  ) : (
                    <ValidationBadge v={e.validation} full />
                  )}
                </span>
              </label>
            ))}
          </div>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <button type="button" onClick={add} disabled={selected.size === 0 || !!busy}>
              Add {selected.size} reference{selected.size === 1 ? "" : "s"}
            </button>
            <button className="secondary" type="button" onClick={() => setEntries(null)}>
              Edit text
            </button>
          </div>
        </>
      )}
      {busy && <p className="muted">{busy}</p>}
      {err && <p className="error">{err}</p>}
    </div>
  );
}
