// Text search over a PDF's text layer. pdf.js splits a page into text items (runs of text,
// often one per line); search runs over each page's joined text so phrases can span items,
// and highlightItem maps the hits back onto a single item for react-pdf's customTextRenderer.

export type TextItemLike = { str?: string; hasEOL?: boolean; type?: string };
export type Range = [start: number, end: number];

// `starts[i]` is where item i begins in `text`, or -1 for marked-content items (no text),
// so indexes line up with react-pdf's `itemIndex`.
export function buildPageText(items: TextItemLike[]): { text: string; starts: number[] } {
  let text = "";
  const starts = items.map((item) => {
    if (typeof item.str !== "string") return -1;
    const start = text.length;
    text += item.str;
    if (item.hasEOL) text += " ";
    return start;
  });
  return { text, starts };
}

// Case-insensitive, non-overlapping; any whitespace in the query matches any run of whitespace.
export function findMatches(text: string, query: string): Range[] {
  const words = query.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const pattern = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
  return Array.from(text.matchAll(new RegExp(pattern, "gi")), (m) => [
    m.index,
    m.index + m[0].length,
  ]);
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// HTML for one text item starting at `itemStart` in the page text, with every overlapping
// match wrapped in <mark>; `current` is the index (into `matches`) of the selected match.
export function highlightItem(
  str: string,
  itemStart: number,
  matches: Range[],
  current: number | null,
): string {
  const itemEnd = itemStart + str.length;
  let html = "";
  let pos = 0;
  matches.forEach(([start, end], i) => {
    if (end <= itemStart || start >= itemEnd) return;
    const from = Math.max(start, itemStart) - itemStart;
    const to = Math.min(end, itemEnd) - itemStart;
    html += escapeHtml(str.slice(pos, from));
    html += `<mark class="pdf-hit${i === current ? " current" : ""}">${escapeHtml(str.slice(from, to))}</mark>`;
    pos = to;
  });
  return html + escapeHtml(str.slice(pos));
}
