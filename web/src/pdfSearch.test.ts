import { describe, expect, it } from "vitest";
import { buildPageText, findMatches, highlightItem } from "./pdfSearch";

describe("buildPageText", () => {
  it("joins text items, adds a space at line ends, and skips marked-content items", () => {
    const page = buildPageText([
      { str: "Deep", hasEOL: false },
      { type: "beginMarkedContent" },
      { str: " learning", hasEOL: true },
      { str: "for fish", hasEOL: false },
    ]);
    expect(page.text).toBe("Deep learning for fish");
    expect(page.starts).toEqual([0, -1, 4, 14]);
  });
});

describe("findMatches", () => {
  it("finds case-insensitive, non-overlapping matches", () => {
    expect(findMatches("Fish fish FISH", "fish")).toEqual([[0, 4], [5, 9], [10, 14]]);
    expect(findMatches("aaaa", "aa")).toEqual([[0, 2], [2, 4]]);
  });

  it("returns nothing for a blank query", () => {
    expect(findMatches("anything", "   ")).toEqual([]);
  });

  it("matches across a line break and ignores repeated whitespace in the query", () => {
    const { text } = buildPageText([
      { str: "deep", hasEOL: true },
      { str: "learning", hasEOL: false },
    ]);
    expect(findMatches(text, "deep  learning")).toEqual([[0, 13]]);
  });
});

describe("highlightItem", () => {
  it("wraps the part of a match that falls inside an item and escapes HTML", () => {
    // page text: "a <b> fish" + "y tale"; query "fishy" spans both items
    const html = highlightItem("a <b> fish", 0, [[6, 11]], null);
    expect(html).toBe('a &lt;b&gt; <mark class="pdf-hit">fish</mark>');
    expect(highlightItem("y tale", 10, [[6, 11]], null)).toBe('<mark class="pdf-hit">y</mark> tale');
  });

  it("marks the current match differently", () => {
    expect(highlightItem("fish fish", 0, [[0, 4], [5, 9]], 1)).toBe(
      '<mark class="pdf-hit">fish</mark> <mark class="pdf-hit current">fish</mark>',
    );
  });

  it("returns the escaped item when nothing matches", () => {
    expect(highlightItem("x & y", 0, [], null)).toBe("x &amp; y");
  });
});
