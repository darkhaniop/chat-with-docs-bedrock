import { describe, expect, it } from "vitest";
import { pagesToRender } from "./viewerVirtualization";

describe("pagesToRender", () => {
  it("renders the current page plus one on either side", () => {
    expect(pagesToRender(5, 10)).toEqual([4, 5, 6]);
  });

  it("clamps at the start of the document", () => {
    expect(pagesToRender(1, 10)).toEqual([1, 2]);
  });

  it("clamps at the end of the document", () => {
    expect(pagesToRender(10, 10)).toEqual([9, 10]);
  });

  it("handles a single-page document", () => {
    expect(pagesToRender(1, 1)).toEqual([1]);
  });
});
