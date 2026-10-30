// The page a typed-in page number asks for, clamped to the document; null if the text isn't
// a whole number or the page count isn't known yet.
export function parsePageInput(text: string, numPages: number): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t) || numPages < 1) return null;
  return Math.min(numPages, Math.max(1, Number(t)));
}
