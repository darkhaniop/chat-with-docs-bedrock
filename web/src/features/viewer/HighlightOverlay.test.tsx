import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HighlightOverlay } from "./HighlightOverlay";

describe("HighlightOverlay", () => {
  it("renders one positioned div per rect, decorative and aria-hidden", () => {
    const { container } = render(
      <HighlightOverlay
        rects={[
          { left: 10, top: 20, width: 100, height: 15 },
          { left: 10, top: 40, width: 60, height: 15 },
        ]}
        pulseKey={0}
      />,
    );
    const layer = container.firstElementChild as HTMLElement;
    expect(layer.getAttribute("aria-hidden")).toBe("true");
    expect(layer.children).toHaveLength(2);
    const first = layer.children[0] as HTMLElement;
    expect(first.style.left).toBe("10px");
    expect(first.style.top).toBe("20px");
    expect(first.style.width).toBe("100px");
    expect(first.style.height).toBe("15px");
    expect(first.style.mixBlendMode).toBe("multiply");
  });

  it("remounts highlight elements when pulseKey changes, so the pulse animation can restart", () => {
    const rects = [{ left: 0, top: 0, width: 10, height: 10 }];
    const { container, rerender } = render(<HighlightOverlay rects={rects} pulseKey={0} />);
    const firstKeyEl = container.querySelector('[aria-hidden="true"] > div')!;

    rerender(<HighlightOverlay rects={rects} pulseKey={1} />);
    const secondKeyEl = container.querySelector('[aria-hidden="true"] > div')!;

    // React only actually remounts (rather than patching) an element when its `key` changes;
    // this is a proxy for that since jsdom doesn't run real CSS animations.
    expect(firstKeyEl).not.toBe(secondKeyEl);
  });

  it("renders nothing but the empty overlay layer when there are no rects", () => {
    const { container } = render(<HighlightOverlay rects={[]} pulseKey={0} />);
    expect(container.firstElementChild?.children).toHaveLength(0);
  });
});
