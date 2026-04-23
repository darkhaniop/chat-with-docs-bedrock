import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ImageViewer } from "./ImageViewer";
import type { DocumentPage } from "../../api/types";

const page: DocumentPage = {
  pageNumber: 1,
  width: 1000,
  height: 2000,
  rotation: 0,
  textSource: "textract",
};

// `<img alt="">` (decorative — docs/06's overlay carries the accessible content, not the image
// itself) has no accessible "img" role, so these tests query the element directly rather than
// through `getByRole`.
function loadImageAt(container: HTMLElement, width: number, height: number) {
  const img = container.querySelector("img")!;
  Object.defineProperty(img, "clientWidth", { value: width, configurable: true });
  Object.defineProperty(img, "clientHeight", { value: height, configurable: true });
  fireEvent.load(img);
}

describe("ImageViewer", () => {
  it("renders the render-url image", () => {
    const { container } = render(
      <ImageViewer renderUrl="https://example.com/render.jpg" page={page} selection={null} />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", "https://example.com/render.jpg");
  });

  it("draws no highlight before the image has reported its displayed size", () => {
    const { container } = render(
      <ImageViewer
        renderUrl="https://example.com/render.jpg"
        page={page}
        selection={{ documentId: "doc-1", pageNumber: 1, rects: [[0, 0, 10, 10]], nonce: 1 }}
      />,
    );
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
  });

  it("scales a selected rect against the image's displayed (not natural) size once loaded", () => {
    const { container } = render(
      <ImageViewer
        renderUrl="https://example.com/render.jpg"
        page={page}
        selection={{ documentId: "doc-1", pageNumber: 1, rects: [[100, 200, 300, 250]], nonce: 1 }}
      />,
    );
    loadImageAt(container, 500, 1000); // half of the canonical 1000x2000

    const highlight = container.querySelector('[aria-hidden="true"] > div') as HTMLElement;
    expect(highlight.style.left).toBe("50px");
    expect(highlight.style.top).toBe("100px");
    expect(highlight.style.width).toBe("100px");
    expect(highlight.style.height).toBe("25px");
  });

  it("shows no highlight for a selection on a different page", () => {
    const { container } = render(
      <ImageViewer
        renderUrl="https://example.com/render.jpg"
        page={page}
        selection={{ documentId: "doc-1", pageNumber: 2, rects: [[0, 0, 10, 10]], nonce: 1 }}
      />,
    );
    loadImageAt(container, 500, 1000);
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});
