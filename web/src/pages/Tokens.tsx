import { FormEvent, useEffect, useState } from "react";
import { API_URL, api } from "../api";
import type { ApiToken, ApiTokenCreated } from "../types";

const EXPIRY_OPTIONS: { label: string; days: number }[] = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
];

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "—");
const isExpired = (t: ApiToken) => new Date(t.expires_at) <= new Date();

export default function Tokens() {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState(1);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");

  const load = () => api.listTokens().then(setTokens).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setErr("");
    try {
      setCreated(await api.createToken(name.trim(), EXPIRY_OPTIONS[expiry].days));
      setCopied(false);
      setName("");
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const revoke = async (t: ApiToken) => {
    if (!confirm(`Revoke "${t.name}"? Anything using it will stop working.`)) return;
    setErr("");
    try {
      await api.revokeToken(t.id);
      if (created?.id === t.id) setCreated(null);
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.token);
      setCopied(true);
    } catch {
      /* clipboard blocked: the token is still selectable */
    }
  };

  return (
    <div>
      <h1>API tokens</h1>
      <p className="muted">
        Tokens let scripts and AI agents use the API as you, with your library permissions.
        Send one as <code>Authorization: Bearer &lt;token&gt;</code> to <code>{API_URL}</code>.
      </p>
      {err && <p className="error">{err}</p>}
      <form className="card row" onSubmit={create}>
        <input
          className="grow"
          placeholder="Name, e.g. Claude on my laptop"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select value={expiry} onChange={(e) => setExpiry(Number(e.target.value))}>
          {EXPIRY_OPTIONS.map((o, i) => (
            <option key={o.label} value={i}>
              Expires: {o.label}
            </option>
          ))}
        </select>
        <button type="submit">Create token</button>
      </form>
      {created && (
        <div className="card">
          <strong>Copy your new token now. It won't be shown again.</strong>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <input className="grow" readOnly value={created.token} onFocus={(e) => e.target.select()} />
            <button type="button" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      )}
      {tokens.length === 0 && <p className="muted">No tokens yet.</p>}
      {tokens.length > 0 && (
        <div className="card">
          {tokens.map((t) => (
            <div className="item-row" key={t.id}>
              <span className="grow">
                <strong>{t.name}</strong> <code className="muted">{t.prefix}…</code>
                {isExpired(t) && <> <span className="tag expired">Expired</span></>}
                <br />
                <span className="muted">
                  Created {fmt(t.created_at)} · Last used {fmt(t.last_used_at)} ·{" "}
                  {isExpired(t) ? "Expired" : "Expires"} {fmt(t.expires_at)}
                </span>
              </span>
              <button className="secondary" type="button" onClick={() => revoke(t)}>
                {isExpired(t) ? "Delete" : "Revoke"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
