import { useEffect, useRef, useState } from "react";
import { fetchText } from "../api";

/** Fetch `url`'s BibTeX and put it on the clipboard.
 *
 * Safari only allows a clipboard write during the click that asked for it, and the
 * export has to be fetched first. A ClipboardItem built from a *promise* is accepted
 * synchronously and filled in when the fetch resolves, so it works everywhere that
 * supports it; elsewhere fall back to writeText once the text has arrived. */
async function copyBibtex(url: string): Promise<void> {
  const text = fetchText(url);
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    try {
      const blob = text.then((t) => new Blob([t], { type: "text/plain" }));
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      return;
    } catch (e) {
      // A failed fetch surfaces here too: report it rather than retrying the copy.
      await text;
      if (!navigator.clipboard?.writeText) throw e;
    }
  }
  if (!navigator.clipboard?.writeText) {
    throw new Error("This browser doesn't allow copying to the clipboard");
  }
  await navigator.clipboard.writeText(await text);
}

type Status = { kind: "idle" } | { kind: "copying" } | { kind: "copied" } | { kind: "error"; msg: string };

/** "Export .bib" split button: the main part downloads the file; the ▾ opens a menu that
 * can also copy the BibTeX to the clipboard. */
export default function ExportBibButton({ url, label = "Export .bib" }: { url: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const root = useRef<HTMLDivElement>(null);
  const resetTimer = useRef<number | undefined>(undefined);

  // Close the menu on an outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => () => window.clearTimeout(resetTimer.current), []);

  const showFor = (s: Status, ms: number) => {
    setStatus(s);
    window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setStatus({ kind: "idle" }), ms);
  };

  const copy = async () => {
    setOpen(false);
    setStatus({ kind: "copying" });
    try {
      await copyBibtex(url);
      showFor({ kind: "copied" }, 2000);
    } catch (e) {
      showFor({ kind: "error", msg: e instanceof Error ? e.message : String(e) }, 6000);
    }
  };

  const mainLabel =
    status.kind === "copying" ? "Copying…" : status.kind === "copied" ? "Copied ✓" : label;

  return (
    <div className="split-button" ref={root}>
      <a href={url} className="split-main">
        <button className="secondary" type="button">
          {mainLabel}
        </button>
      </a>
      <button
        className="secondary split-toggle"
        type="button"
        aria-label="More export options"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        ▾
      </button>
      {open && (
        <div className="split-menu" role="menu">
          <a href={url} role="menuitem" onClick={() => setOpen(false)}>
            Download .bib file
          </a>
          <button type="button" role="menuitem" onClick={copy}>
            Copy BibTeX to clipboard
          </button>
        </div>
      )}
      {status.kind === "error" && (
        <span className="error split-error" role="alert">
          Couldn't copy: {status.msg}
        </span>
      )}
    </div>
  );
}
