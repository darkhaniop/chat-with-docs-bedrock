import * as path from "node:path";
import { expect, test } from "../fixtures/authenticatedPage";

/**
 * docs/08-testing.md: "Upload a scanned fixture, ask a question, assert a citation resolves
 * (proves the OCR path end to end)." `scanned.pdf` has no PDF text layer
 * (docs/00's Phase 0 fixture corpus), so `textDensity` < the Phase 3 threshold and every rect
 * on this document's chunks comes from Textract's `DetectDocumentText`, converted through
 * `common/geometry.py`'s Textract-normalised -> canonical conversion — a citation resolving at
 * all here, with a plausible highlight, is what proves that whole path end to end rather than
 * just at the unit-test level (`test_ocr.py`, `test_textract_geometry`).
 *
 * Still renders through `PdfViewer`/pdf.js, not `ImageViewer` — `scanned.pdf`'s `kind` is
 * `"pdf"` regardless of how its text was extracted; only a standalone image upload
 * (`photograph.jpg`) is a `kind: "image"` document, which `ImageViewer.test.tsx` already covers
 * at the unit level.
 */

const QUESTION = "Who is the scanned letter addressed to?";
const EXPECTED_CITED_SUBSTRING = "Alvarez";
const FIXTURE_PATH = path.resolve(__dirname, "../fixtures/scanned.pdf");

test.describe("scanned document citations", () => {
  test("a citation on a Textract-derived chunk resolves to a plausible highlight", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "chat-with-docs-bedrock" })).toBeVisible();

    const projectName = `e2e-scanned-${Date.now()}`;
    await page.getByLabel("New project name").fill(projectName);
    await page.getByRole("button", { name: "Add" }).click();

    await expect(page.getByLabel("Upload documents")).toBeVisible({ timeout: 15_000 });
    await page.locator('input[type="file"]').setInputFiles(FIXTURE_PATH);

    const documentRow = page.locator("li", { hasText: "scanned.pdf" });
    // Textract's synchronous DetectDocumentText call adds real latency over the pure-PDF-text
    // path `chat-citations.spec.ts` exercises — same generous ceiling as that spec regardless.
    await expect(documentRow.getByText("Ready")).toBeVisible({ timeout: 120_000 });

    await page.getByPlaceholder(/ask a question/i).fill(QUESTION);
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 120_000 });

    const citationButton = page.getByRole("button", { name: /^\[\d+\]$/ }).first();
    await expect(citationButton).toBeVisible({ timeout: 15_000 });

    const describedById = await citationButton.getAttribute("aria-describedby");
    expect(describedById).toBeTruthy();
    await expect(page.locator(`#${describedById}`)).toContainText(EXPECTED_CITED_SUBSTRING);

    await citationButton.click();
    await expect(page.getByRole("button", { name: "Close viewer" })).toBeVisible({
      timeout: 15_000,
    });

    const highlight = page.locator('[data-page-number] [aria-hidden="true"] > div').first();
    await expect(highlight).toBeVisible({ timeout: 10_000 });
    const box = await highlight.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);

    await page.getByRole("button", { name: "Close viewer" }).click();
    await page.getByRole("button", { name: `Delete ${projectName}` }).click();
    await expect(page.getByText(projectName)).toHaveCount(0);
  });
});
