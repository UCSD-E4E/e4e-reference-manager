import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { api } from "../api";
import type { Annotation } from "../types";

export default function PdfViewer({
  url,
  attachmentId,
  canEdit = false,
}: {
  url: string;
  attachmentId?: string;
  canEdit?: boolean;
}) {
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [width, setWidth] = useState(800);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [err, setErr] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const update = () => {
      const w = ref.current?.clientWidth ?? 800;
      setWidth(Math.max(280, Math.min(900, w - 24)));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const loadAnnotations = useCallback(() => {
    if (!attachmentId) return;
    api
      .listAnnotations(attachmentId)
      .then(setAnnotations)
      .catch((e) => setErr(String(e)));
  }, [attachmentId]);

  useEffect(() => loadAnnotations(), [loadAnnotations]);

  // Turn the current text selection into a page + normalized rectangles (0..1 relative to
  // the rendered page), so highlights survive zoom/width changes.
  const highlightSelection = () => {
    if (!attachmentId) return;
    const sel = window.getSelection();
    const pageEl = frameRef.current?.querySelector(".react-pdf__Page") as HTMLElement | null;
    if (!sel || sel.isCollapsed || !pageEl) return;
    const box = pageEl.getBoundingClientRect();
    const rects = Array.from(sel.getRangeAt(0).getClientRects())
      .map((r) => ({
        x: (r.left - box.left) / box.width,
        y: (r.top - box.top) / box.height,
        w: r.width / box.width,
        h: r.height / box.height,
      }))
      .filter((r) => r.w > 0.002 && r.h > 0.002 && r.x >= -0.02 && r.y >= -0.02);
    if (!rects.length) return;
    const quote = sel.toString().trim();
    const comment = window.prompt("Add a comment for this highlight (optional):", "") ?? "";
    api
      .createAnnotation(attachmentId, { page, rects, quote, comment, color: "#ffd54f" })
      .then(() => {
        sel.removeAllRanges();
        loadAnnotations();
      })
      .catch((e) => setErr(String(e)));
  };

  const del = (id: string) =>
    api.deleteAnnotation(id).then(loadAnnotations).catch((e) => setErr(String(e)));

  const pageAnnotations = annotations.filter((a) => a.page === page);

  return (
    <div ref={ref}>
      <div className="pdf-toolbar">
        <button
          className="secondary"
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
        >
          ‹ Prev
        </button>
        <span className="muted">
          Page {page} {numPages ? `/ ${numPages}` : ""}
        </span>
        <button
          className="secondary"
          onClick={() => setPage((p) => Math.min(numPages || p, p + 1))}
          disabled={page >= numPages}
        >
          Next ›
        </button>
        {canEdit && attachmentId && (
          <button type="button" onClick={highlightSelection} title="Select text in the PDF, then click">
            Highlight selection
          </button>
        )}
      </div>
      {err && <p className="error">{err}</p>}
      <div className="pdf-frame" ref={frameRef}>
        <Document
          file={url}
          onLoadSuccess={({ numPages }) => setNumPages(numPages)}
          loading="Loading PDF…"
          error="Could not load this PDF."
        >
          <Page pageNumber={page} width={width}>
            {pageAnnotations.flatMap((a) =>
              a.rects.map((r, i) => (
                <div
                  key={`${a.id}-${i}`}
                  title={a.comment || a.quote || "highlight"}
                  style={{
                    position: "absolute",
                    left: `${r.x * 100}%`,
                    top: `${r.y * 100}%`,
                    width: `${r.w * 100}%`,
                    height: `${r.h * 100}%`,
                    background: a.color,
                    opacity: 0.35,
                    pointerEvents: "none",
                    mixBlendMode: "multiply",
                  }}
                />
              )),
            )}
          </Page>
        </Document>
      </div>

      {attachmentId && (
        <div style={{ marginTop: "0.5rem" }}>
          <h3 style={{ margin: "0.25rem 0" }}>Highlights</h3>
          {annotations.length === 0 && (
            <p className="muted">
              No highlights yet.
              {canEdit ? " Select text in the PDF and click “Highlight selection”." : ""}
            </p>
          )}
          {annotations.map((a) => (
            <div className="item-row" key={a.id}>
              <span className="grow">
                <button
                  className="secondary"
                  type="button"
                  style={{ padding: "0.1rem 0.4rem", marginRight: "0.4rem" }}
                  onClick={() => setPage(a.page)}
                >
                  p.{a.page}
                </button>
                {a.quote && <em>“{a.quote.slice(0, 90)}”</em>}
                {a.comment && <span> — {a.comment}</span>}
              </span>
              {canEdit && (
                <button className="secondary" type="button" onClick={() => del(a.id)}>
                  Delete
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
