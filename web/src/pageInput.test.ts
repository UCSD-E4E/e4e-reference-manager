import { describe, expect, it } from "vitest";
import { pageLabel, parsePageInput } from "./pageInput";

describe("parsePageInput", () => {
  it("reads a page number", () => {
    expect(parsePageInput("7", 20)).toBe(7);
    expect(parsePageInput("  12 ", 20)).toBe(12);
  });

  it("clamps to the document", () => {
    expect(parsePageInput("999", 20)).toBe(20);
    expect(parsePageInput("0", 20)).toBe(1);
  });

  it("rejects anything that isn't a whole number", () => {
    for (const text of ["", "  ", "abc", "3.5", "-2", "1e3", "4a"]) {
      expect(parsePageInput(text, 20)).toBeNull();
    }
  });

  it("rejects input before the page count is known", () => {
    expect(parsePageInput("3", 0)).toBeNull();
  });

  // A book: cover, i–iii of front matter, then 1–4.
  const labels = ["Cover", "i", "ii", "iii", "1", "2", "3", "4"];

  it("goes to the page carrying a typed label", () => {
    expect(parsePageInput("1", 8, labels)).toBe(5);
    expect(parsePageInput("ii", 8, labels)).toBe(3);
    expect(parsePageInput(" III ", 8, labels)).toBe(4);
    expect(parsePageInput("cover", 8, labels)).toBe(1);
  });

  it("falls back to the physical page when no label matches", () => {
    expect(parsePageInput("7", 8, labels)).toBe(7);
    expect(parsePageInput("99", 8, labels)).toBe(8);
    expect(parsePageInput("xiv", 8, labels)).toBeNull();
  });
});

describe("pageLabel", () => {
  it("is the PDF's label for the page", () => {
    expect(pageLabel(2, ["i", "ii", "1"])).toBe("ii");
  });

  it("is the page number without labels, or for a blank one", () => {
    expect(pageLabel(3, null)).toBe("3");
    expect(pageLabel(2, ["i", "", "1"])).toBe("2");
    expect(pageLabel(5, ["i", "ii"])).toBe("5");
  });
});
