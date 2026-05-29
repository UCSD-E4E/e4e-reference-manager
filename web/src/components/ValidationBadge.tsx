import type { Validation } from "../types";

const STYLES = {
  verified: { icon: "✓", color: "#16a34a", label: "verified" },
  metadata_mismatch: { icon: "⚠", color: "#d97706", label: "title mismatch" },
  not_found: { icon: "✗", color: "#dc2626", label: "not found" },
  unverifiable: { icon: "?", color: "#6b7280", label: "unverifiable" },
} as const;

export default function ValidationBadge({
  v,
  full = false,
}: {
  v: Validation | null | undefined;
  full?: boolean;
}) {
  if (!v) {
    return full ? <span className="muted">unvalidated</span> : null;
  }
  const s = STYLES[v.status];
  const title = `${s.label} (${v.source}): ${v.notes}`;
  return (
    <span
      title={title}
      style={{ color: s.color, marginLeft: full ? 0 : "0.4rem", fontWeight: 600 }}
    >
      {s.icon}
      {full ? ` ${s.label}` : ""}
    </span>
  );
}
