import { useEffect, useRef, useState } from "react";
import { Document, Page } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

export default function PdfViewer({ url }: { url: string }) {
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [width, setWidth] = useState(800);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const update = () => {
      const w = ref.current?.clientWidth ?? 800;
      setWidth(Math.max(280, Math.min(900, w - 24)));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

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
      </div>
      <div className="pdf-frame">
        <Document
          file={url}
          onLoadSuccess={({ numPages }) => setNumPages(numPages)}
          loading="Loading PDF…"
          error="Could not load this PDF."
        >
          <Page pageNumber={page} width={width} />
        </Document>
      </div>
    </div>
  );
}
