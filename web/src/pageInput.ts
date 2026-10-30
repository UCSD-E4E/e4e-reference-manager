type Labels = readonly string[] | null | undefined;

// The page a typed-in page asks for. With page labels (books numbering front matter i, ii,
// ... then 1, 2, ...) a label wins, matched case-insensitively; otherwise a whole number is
// the physical page, clamped to the document. Null if nothing fits or the page count isn't
// known yet.
export function parsePageInput(text: string, numPages: number, labels?: Labels): number | null {
  const t = text.trim();
  if (!t || numPages < 1) return null;
  const want = t.toLowerCase();
  const i = labels?.findIndex((l) => l.trim().toLowerCase() === want) ?? -1;
  if (i >= 0 && i < numPages) return i + 1;
  if (!/^\d+$/.test(t)) return null;
  return Math.min(numPages, Math.max(1, Number(t)));
}

// What to call a page: the PDF's label for it, or its number when it has none.
export function pageLabel(page: number, labels: Labels): string {
  return labels?.[page - 1]?.trim() || String(page);
}
