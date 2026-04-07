import { describe, expect, it } from "vitest";
import { splitTextByCitations } from "./citations";
import type { Citation } from "../api/types";

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    citationId: "c0",
    documentId: "doc1",
    pageNumber: 1,
    chunkId: "chunk1",
    startSentence: 0,
    endSentence: 1,
    citedText: "cited",
    rects: [],
    spanStart: 0,
    spanEnd: 5,
    suspect: false,
    ...overrides,
  };
}

describe("splitTextByCitations", () => {
  it("returns one uncited run for text with no citations", () => {
    const runs = splitTextByCitations("hello world", []);
    expect(runs).toEqual([{ text: "hello world", citations: [] }]);
  });

  it("returns nothing for empty text with no citations", () => {
    expect(splitTextByCitations("", [])).toEqual([]);
  });

  it("splits a single citation covering the whole string", () => {
    const c = citation({ spanStart: 0, spanEnd: 5 });
    const runs = splitTextByCitations("hello", [c]);
    expect(runs).toEqual([{ text: "hello", citations: [c] }]);
  });

  it("splits a citation covering only part of the string", () => {
    const c = citation({ spanStart: 6, spanEnd: 11 });
    const runs = splitTextByCitations("hello world", [c]);
    expect(runs).toEqual([
      { text: "hello ", citations: [] },
      { text: "world", citations: [c] },
    ]);
  });

  it("handles adjacent citations without gaps or overlap", () => {
    const first = citation({ citationId: "c0", spanStart: 0, spanEnd: 5 });
    const second = citation({ citationId: "c1", spanStart: 5, spanEnd: 11 });
    const runs = splitTextByCitations("hello world", [first, second]);
    expect(runs).toEqual([
      { text: "hello", citations: [first] },
      { text: " world", citations: [second] },
    ]);
  });

  it("attaches multiple citations that share the exact same span", () => {
    const first = citation({ citationId: "c0", spanStart: 0, spanEnd: 5 });
    const second = citation({ citationId: "c1", spanStart: 0, spanEnd: 5 });
    const runs = splitTextByCitations("hello", [first, second]);
    expect(runs).toEqual([{ text: "hello", citations: [first, second] }]);
  });

  it("clamps a citation span that runs past the end of the text", () => {
    const c = citation({ spanStart: 0, spanEnd: 999 });
    const runs = splitTextByCitations("short", [c]);
    expect(runs).toEqual([{ text: "short", citations: [c] }]);
  });
});
