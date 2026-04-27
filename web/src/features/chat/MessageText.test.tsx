import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageText } from "./MessageText";
import type { Citation } from "../../api/types";

function citation(overrides: Partial<Citation>): Citation {
  return {
    citationId: "c0",
    documentId: "doc-1",
    pageNumber: 1,
    chunkId: "chunk-1",
    startSentence: 0,
    endSentence: 1,
    citedText: "The facility achieved 94% uptime in Q3.",
    rects: [[72, 640.2, 511.4, 655.8]],
    spanStart: 0,
    spanEnd: 10,
    suspect: false,
    ...overrides,
  };
}

describe("MessageText", () => {
  it("calls onCitationClick with the clicked citation", async () => {
    const onCitationClick = vi.fn();
    render(
      <MessageText
        text="Uptime was 94%."
        citations={[citation({ citationId: "c0", spanStart: 0, spanEnd: 15 })]}
        onCitationClick={onCitationClick}
      />,
    );
    screen.getByRole("button", { name: "[0]" }).click();
    expect(onCitationClick).toHaveBeenCalledWith(
      citation({ citationId: "c0", spanStart: 0, spanEnd: 15 }),
    );
  });

  it("moves focus between citation buttons with the arrow keys, wrapping at the ends", () => {
    render(
      <MessageText
        text="First claim. Second claim."
        citations={[
          citation({ citationId: "c0", spanStart: 0, spanEnd: 12 }),
          citation({ citationId: "c1", spanStart: 13, spanEnd: 27 }),
        ]}
      />,
    );
    const first = screen.getByRole("button", { name: "[0]" });
    const second = screen.getByRole("button", { name: "[1]" });

    first.focus();
    expect(document.activeElement).toBe(first);
    first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(document.activeElement).toBe(second);
    second.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(document.activeElement).toBe(first); // wraps
  });

  it("exposes citedText via aria-describedby, not only the title attribute", () => {
    render(<MessageText text="Uptime was 94%." citations={[citation({ citationId: "c0" })]} />);
    const button = screen.getByRole("button", { name: "[0]" });
    const describedById = button.getAttribute("aria-describedby");
    expect(describedById).toBeTruthy();
    expect(document.getElementById(describedById!)?.textContent).toBe(
      "The facility achieved 94% uptime in Q3.",
    );
  });

  it("overrides the inherited sup{line-height:0} with an explicit line-height", () => {
    render(<MessageText text="Uptime was 94%." citations={[citation({ citationId: "c0" })]} />);
    const button = screen.getByRole("button", { name: "[0]" });
    expect(button.className).toContain("leading-none");
  });
});
