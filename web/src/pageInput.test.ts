import { describe, expect, it } from "vitest";
import { parsePageInput } from "./pageInput";

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
});
