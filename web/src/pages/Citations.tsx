import { PointerEvent as ReactPointerEvent, WheelEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  SimulationLinkDatum,
  SimulationNodeDatum,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";
import { api } from "../api";
import type { CitationGraph, CitationNode, Library } from "../types";

// ── Encoding ─────────────────────────────────────────────────────────────────────────
// Colour = publication year: one-hue blue ramp, light (older) → dark (newer). Ordinal
// steps validated (dataviz validate_palette --ordinal, surface #ffffff): monotone
// lightness, visible gaps, lightest step 2.1:1 against the surface.
const YEAR_STEPS = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"];
const NO_YEAR = "#898781"; // muted ink: "unknown", never a ramp step
const EDGE = "#c3c2b7"; // recessive baseline grey
const EDGE_ACTIVE = "#52514e"; // secondary ink, for a hovered paper's links
const INK = "#0b0b0b";
const INK_2 = "#52514e";
const SURFACE = "#fcfcfb";

// Size = times cited within this library (area grows with the count).
const radius = (n: CitationNode) => 5 + 3 * Math.sqrt(n.cited_by);
const LABELLED = 8; // direct labels only on the most-cited papers

type SimNode = CitationNode & SimulationNodeDatum;
type SimLink = SimulationLinkDatum<SimNode> & { sources: string[] };

/** Bin each distinct year into the 5 ramp steps by rank, so one old outlier doesn't
 * wash every other paper into the same colour. */
function yearScale(nodes: CitationNode[]) {
  const years = [...new Set(nodes.map((n) => n.year).filter((y): y is number => y != null))].sort(
    (a, b) => a - b,
  );
  const bin = new Map<number, number>();
  years.forEach((y, i) => bin.set(y, Math.min(YEAR_STEPS.length - 1, Math.floor((i * YEAR_STEPS.length) / years.length))));
  const ranges = YEAR_STEPS.map((color, s) => {
    const ys = years.filter((y) => bin.get(y) === s);
    return { color, from: ys[0], to: ys[ys.length - 1] };
  }).filter((r) => r.from != null);
  const color = (n: CitationNode) => (n.year == null ? NO_YEAR : YEAR_STEPS[bin.get(n.year)!]);
  return { color, ranges };
}

function layout(graph: CitationGraph, showIsolated: boolean) {
  const keep = (n: CitationNode) => showIsolated || n.cites > 0 || n.cited_by > 0;
  const nodes: SimNode[] = graph.nodes.filter(keep).map((n) => ({ ...n }));
  const ids = new Set(nodes.map((n) => n.id));
  const links: SimLink[] = graph.edges
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ source: e.source, target: e.target, sources: e.sources }));
  forceSimulation(nodes)
    .force("link", forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(70))
    .force("charge", forceManyBody().strength(-90))
    .force("x", forceX(0).strength(0.06))
    .force("y", forceY(0).strength(0.06))
    .force("collide", forceCollide<SimNode>((d) => radius(d) + 4))
    .stop()
    .tick(320);
  const xs = nodes.map((n) => n.x ?? 0);
  const ys = nodes.map((n) => n.y ?? 0);
  const pad = 40;
  const box = nodes.length
    ? {
        x: Math.min(...xs) - pad,
        y: Math.min(...ys) - pad,
        w: Math.max(...xs) - Math.min(...xs) + 2 * pad,
        h: Math.max(...ys) - Math.min(...ys) + 2 * pad,
      }
    : { x: -100, y: -100, w: 200, h: 200 };
  return { nodes, links: links as (SimLink & { source: SimNode; target: SimNode })[], box };
}

/** Line from `a` to the edge of `b`'s circle (so the arrowhead isn't hidden). */
function edgePath(a: SimNode, b: SimNode) {
  const dx = (b.x ?? 0) - (a.x ?? 0);
  const dy = (b.y ?? 0) - (a.y ?? 0);
  const len = Math.hypot(dx, dy) || 1;
  const end = len - radius(b) - 3;
  return {
    x1: a.x ?? 0,
    y1: a.y ?? 0,
    x2: (a.x ?? 0) + (dx * end) / len,
    y2: (a.y ?? 0) + (dy * end) / len,
  };
}

export default function Citations() {
  const { libId = "" } = useParams();
  const navigate = useNavigate();
  const [lib, setLib] = useState<Library | null>(null);
  const [graph, setGraph] = useState<CitationGraph | null>(null);
  const [showIsolated, setShowIsolated] = useState(false);
  const [building, setBuilding] = useState("");
  const [err, setErr] = useState("");
  const [hover, setHover] = useState<{ node: SimNode; x: number; y: number } | null>(null);
  const [view, setView] = useState({ k: 1, x: 0, y: 0 }); // zoom/pan on top of the fit
  const wrap = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const canEdit = lib?.my_access === "edit" || lib?.my_access === "manage";

  const load = () => api.citationGraph(libId).then(setGraph).catch((e) => setErr(String(e)));
  useEffect(() => {
    api.getLibrary(libId).then(setLib).catch((e) => setErr(String(e)));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libId]);

  const laid = useMemo(() => (graph ? layout(graph, showIsolated) : null), [graph, showIsolated]);
  const years = useMemo(() => (graph ? yearScale(graph.nodes) : null), [graph]);
  useEffect(() => setView({ k: 1, x: 0, y: 0 }), [laid]);

  const build = async () => {
    setErr("");
    let after: string | null = null;
    let checked = 0;
    let links = 0;
    let failed = 0;
    try {
      do {
        setBuilding(`Finding citations… ${checked} of ${graph?.total ?? "?"} papers checked`);
        const r = await api.buildCitations(libId, after);
        checked += r.checked;
        links += r.links;
        failed += r.failed;
        after = r.next_after;
      } while (after);
      setBuilding(
        `Checked ${checked} papers, found ${links} citation links` +
          (failed ? ` (${failed} couldn't be looked up; try again later)` : "") +
          ".",
      );
      await load();
    } catch (e) {
      setBuilding("");
      setErr(String(e));
    }
  };

  // ── zoom & pan ──
  const toViewBox = (dxPx: number, dyPx: number) => {
    const rect = svg.current!.getBoundingClientRect();
    const s = laid!.box.w / view.k / rect.width;
    return { dx: dxPx * s, dy: dyPx * s };
  };
  const onWheel = (e: WheelEvent) => {
    if (!laid || !svg.current) return;
    const rect = svg.current.getBoundingClientRect();
    const k = Math.min(8, Math.max(0.5, view.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    // Keep the point under the cursor fixed.
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    const w0 = laid.box.w / view.k;
    const h0 = laid.box.h / view.k;
    setView({
      k,
      x: view.x + fx * (w0 - laid.box.w / k),
      y: view.y + fy * (h0 - laid.box.h / k),
    });
  };
  const onPointerDown = (e: ReactPointerEvent) => {
    if ((e.target as Element).closest("[data-node]")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    if (!drag.current || !laid) return;
    const { dx, dy } = toViewBox(e.clientX - drag.current.x, e.clientY - drag.current.y);
    setView((v) => ({ ...v, x: drag.current!.vx - dx, y: drag.current!.vy - dy }));
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  const showTip = (node: SimNode, e: ReactPointerEvent | React.MouseEvent) => {
    const rect = wrap.current!.getBoundingClientRect();
    setHover({ node, x: e.clientX - rect.left, y: e.clientY - rect.top });
  };

  const labelled = useMemo(() => {
    if (!laid) return new Set<string>();
    const top = [...laid.nodes].filter((n) => n.cited_by > 0).sort((a, b) => b.cited_by - a.cited_by);
    return new Set(top.slice(0, LABELLED).map((n) => n.id));
  }, [laid]);
  const mostCited = useMemo(
    () =>
      graph
        ? [...graph.nodes].filter((n) => n.cited_by > 0).sort((a, b) => b.cited_by - a.cited_by || a.citation_key.localeCompare(b.citation_key)).slice(0, 20)
        : [],
    [graph],
  );
  const maxCited = graph ? Math.max(0, ...graph.nodes.map((n) => n.cited_by)) : 0;

  const active = hover?.node.id;
  const neighbours = useMemo(() => {
    const s = new Set<string>();
    if (laid && active) {
      for (const l of laid.links) {
        if (l.source.id === active) s.add(l.target.id);
        if (l.target.id === active) s.add(l.source.id);
      }
    }
    return s;
  }, [laid, active]);
  const isActiveEdge = (l: { source: SimNode; target: SimNode }) =>
    active != null && (l.source.id === active || l.target.id === active);
  const vb = laid
    ? `${laid.box.x + view.x} ${laid.box.y + view.y} ${laid.box.w / view.k} ${laid.box.h / view.k}`
    : "0 0 1 1";

  return (
    <div>
      <p className="muted">
        <Link to={`/libraries/${libId}`}>← Back to {lib?.name ?? "project"}</Link>
      </p>
      <div className="row">
        <h1 className="grow">Citation graph</h1>
        {canEdit && (
          <button type="button" onClick={build} disabled={building.startsWith("Finding")}>
            {graph?.checked ? "Update citations" : "Build citation graph"}
          </button>
        )}
      </div>
      <p className="muted">
        Each paper in this project is a dot; an arrow points from a paper to a paper it cites.
        Citations come from OpenAlex (papers with a DOI) and from attached PDFs.
        {graph && ` ${graph.checked} of ${graph.total} papers checked.`}
      </p>
      {building && <p className="muted">{building}</p>}
      {err && <p className="error">{err}</p>}

      {graph && graph.checked === 0 && !building && (
        <div className="card">
          <p>
            Citations haven&apos;t been looked up for this project yet.
            {canEdit ? " Build the graph to find them." : " Someone with edit access can build it."}
          </p>
        </div>
      )}

      {laid && graph && graph.checked > 0 && (
        <>
          <div className="row graph-controls">
            <label className="row">
              <input
                type="checkbox"
                checked={showIsolated}
                onChange={(e) => setShowIsolated(e.target.checked)}
                style={{ width: "auto" }}
              />
              Show papers with no citation links
            </label>
            <span className="muted grow">
              {graph.edges.length} links between {laid.nodes.length} papers · scroll to zoom, drag
              to pan, click a paper to open it
            </span>
            {view.k !== 1 || view.x || view.y ? (
              <button className="secondary" type="button" onClick={() => setView({ k: 1, x: 0, y: 0 })}>
                Reset view
              </button>
            ) : null}
          </div>

          {laid.nodes.length === 0 ? (
            <div className="card">
              <p className="muted">No citation links between papers in this project were found.</p>
            </div>
          ) : (
            <div className="graph-wrap" ref={wrap}>
              <svg
                ref={svg}
                className="graph-canvas"
                viewBox={vb}
                role="img"
                aria-label={`Citation graph: ${laid.nodes.length} papers, ${graph.edges.length} citation links`}
                onWheel={onWheel}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerUp}
              >
                <defs>
                  {[
                    ["arrow", EDGE],
                    ["arrow-active", EDGE_ACTIVE],
                  ].map(([id, fill]) => (
                    <marker key={id} id={id} viewBox="0 0 10 10" refX="10" refY="5"
                      markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                      <path d="M0,0 L10,5 L0,10 z" fill={fill} />
                    </marker>
                  ))}
                </defs>
                <g>
                  {laid.links.map((l) => {
                    const on = isActiveEdge(l);
                    return (
                      <line
                        key={`${l.source.id}-${l.target.id}`}
                        {...edgePath(l.source, l.target)}
                        stroke={on ? EDGE_ACTIVE : EDGE}
                        strokeWidth={on ? 2 : 1}
                        strokeOpacity={active && !on ? 0.35 : 1}
                        markerEnd={`url(#${on ? "arrow-active" : "arrow"})`}
                      />
                    );
                  })}
                </g>
                <g>
                  {laid.nodes.map((n) => (
                    <g
                      key={n.id}
                      data-node
                      transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                      style={{ cursor: "pointer" }}
                      opacity={active && active !== n.id && !neighbours.has(n.id) ? 0.4 : 1}
                      onPointerEnter={(e) => showTip(n, e)}
                      onPointerMove={(e) => showTip(n, e)}
                      onPointerLeave={() => setHover(null)}
                      onClick={() => navigate(`/items/${n.id}`)}
                    >
                      {/* bigger invisible hit target than the mark */}
                      <circle r={radius(n) + 6} fill="transparent" />
                      <circle r={radius(n)} fill={years!.color(n)} stroke={SURFACE} strokeWidth={2} />
                      {labelled.has(n.id) && (
                        <text x={radius(n) + 4} y={4} className="graph-label" fill={INK_2}>
                          {n.citation_key}
                        </text>
                      )}
                    </g>
                  ))}
                </g>
              </svg>

              {hover && (
                <div
                  className="graph-tooltip"
                  style={{
                    left: Math.min(hover.x + 14, (wrap.current?.clientWidth ?? 0) - 280),
                    top: hover.y + 14,
                  }}
                >
                  <strong style={{ color: INK }}>{hover.node.title || hover.node.citation_key}</strong>
                  <div className="muted">
                    {[hover.node.authors, hover.node.year].filter(Boolean).join(" · ")}
                  </div>
                  <div>
                    Cites {hover.node.cites} · cited by {hover.node.cited_by} in this project
                    {hover.node.has_pdf ? " · PDF" : ""}
                  </div>
                </div>
              )}

              <div className="graph-legend" aria-label="Legend">
                <div>
                  <span className="muted">Year</span>
                  {years!.ranges.map((r) => (
                    <span key={r.color} className="legend-item">
                      <span className="legend-dot" style={{ background: r.color }} />
                      {r.from === r.to ? r.from : `${r.from}–${r.to}`}
                    </span>
                  ))}
                  {graph.nodes.some((n) => n.year == null) && (
                    <span className="legend-item">
                      <span className="legend-dot" style={{ background: NO_YEAR }} />
                      no year
                    </span>
                  )}
                </div>
                <div>
                  <span className="muted">Size: cited by</span>
                  {[1, 5, 10].filter((c) => c <= Math.max(1, maxCited)).map((c) => (
                    <span key={c} className="legend-item">
                      <svg width={2 * (5 + 3 * Math.sqrt(10)) + 2} height={2 * (5 + 3 * Math.sqrt(c)) + 2}>
                        <circle
                          cx={5 + 3 * Math.sqrt(10) + 1}
                          cy={5 + 3 * Math.sqrt(c) + 1}
                          r={5 + 3 * Math.sqrt(c)}
                          fill="none"
                          stroke={INK_2}
                        />
                      </svg>
                      {c}
                    </span>
                  ))}
                  <span className="legend-item muted">→ cites</span>
                </div>
              </div>
            </div>
          )}

          {mostCited.length > 0 && (
            <div className="card" style={{ marginTop: "1rem" }}>
              <h2>Most cited in this project</h2>
              <table className="graph-table">
                <thead>
                  <tr>
                    <th>Paper</th>
                    <th>Year</th>
                    <th className="num">Cited by</th>
                    <th className="num">Cites</th>
                  </tr>
                </thead>
                <tbody>
                  {mostCited.map((n) => (
                    <tr key={n.id}>
                      <td>
                        <Link to={`/items/${n.id}`}>{n.title || n.citation_key}</Link>
                      </td>
                      <td>{n.year ?? "—"}</td>
                      <td className="num">{n.cited_by}</td>
                      <td className="num">{n.cites}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
