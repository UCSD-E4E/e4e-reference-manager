import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import PdfViewer from "../components/PdfViewer";
import type { Item } from "../types";

function authorsText(csl: Record<string, unknown>): string {
  const authors = csl.author as Array<{ family?: string; given?: string }> | undefined;
  if (!authors) return "";
  return authors.map((a) => [a.given, a.family].filter(Boolean).join(" ")).join(", ");
}

export default function ItemView() {
  const { itemId = "" } = useParams();
  const [item, setItem] = useState<Item | null>(null);
  const [err, setErr] = useState("");
  const [viewUrl, setViewUrl] = useState<string | null>(null);

  const load = () => api.getItem(itemId).then(setItem).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId]);

  const onUpload = async (e: FormEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    setErr("");
    try {
      await api.uploadAttachment(itemId, file);
      load();
    } catch (e) {
      setErr(String(e));
    }
    e.currentTarget.value = "";
  };

  const view = async (attId: string) => {
    try {
      const { url } = await api.attachmentSignedUrl(attId);
      setViewUrl(url);
    } catch (e) {
      setErr(String(e));
    }
  };

  if (!item) return <p className="muted">{err ? <span className="error">{err}</span> : "Loading…"}</p>;

  const csl = item.csl_json;
  return (
    <div>
      <p className="muted">
        <Link to={`/libraries/${item.library_id}`}>← Back to project</Link>
      </p>
      <h1>{item.title || "(untitled)"}</h1>
      {err && <p className="error">{err}</p>}

      <div className="card">
        <div className="muted">
          <span className="tag">{item.type}</span> {item.citation_key}
          {item.year ? ` · ${item.year}` : ""}
        </div>
        {authorsText(csl) && <p>{authorsText(csl)}</p>}
        {typeof csl["container-title"] === "string" && (
          <p className="muted">{csl["container-title"] as string}</p>
        )}
        {item.doi && (
          <p className="muted">
            DOI: <a href={`https://doi.org/${item.doi}`}>{item.doi}</a>
          </p>
        )}
        {typeof csl.abstract === "string" && <p>{csl.abstract as string}</p>}
      </div>

      <div className="card">
        <h2>PDFs</h2>
        <label className="secondary" style={{ padding: "0.55rem 0.7rem", borderRadius: 8, display: "inline-block" }}>
          Upload PDF
          <input type="file" accept="application/pdf" onChange={onUpload} style={{ display: "none" }} />
        </label>
        {item.attachments.length === 0 && <p className="muted">No PDFs attached.</p>}
        {item.attachments.map((a) => (
          <div className="item-row" key={a.id}>
            <div className="grow">
              {a.filename} <span className="muted">({Math.round(a.size / 1024)} KB)</span>
            </div>
            <div className="row">
              <button className="secondary" type="button" onClick={() => view(a.id)}>
                View
              </button>
              <a href={api.attachmentDownloadUrl(a.id)}>
                <button className="secondary" type="button">
                  Download
                </button>
              </a>
            </div>
          </div>
        ))}
      </div>

      {viewUrl && (
        <div className="card">
          <PdfViewer url={viewUrl} />
        </div>
      )}
    </div>
  );
}
