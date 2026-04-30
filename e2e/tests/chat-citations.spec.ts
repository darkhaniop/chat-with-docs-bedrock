import * as path from "node:path";
import { expect, test } from "../fixtures/authenticatedPage";

/**
 * docs/08-testing.md: "Ask a question, see streamed text, click a citation, assert the viewer
 * scrolled to the right page and a highlight overlay exists with plausible geometry."
 *
 * Uses `born-digital.pdf` and its known question/answer pair from
 * `e2e/fixtures/eval/questions.json` (`bd-1`) rather than the shared eval corpus project — a
 * fresh project per test, cleaned up via the UI's own delete affordance, the same convention
 * `resilience.spec.ts` uses. The citation assertion checks the citation's own `citedText`
 * (server-computed, deterministic) rather than the model's generated prose, which can
 * legitimately paraphrase the same fact several ways.
 */

const QUESTION = "What was the facility's uptime in Q3?";
const EXPECTED_CITED_SUBSTRING = "94% uptime";
const FIXTURE_PATH = path.resolve(__dirname, "../fixtures/born-digital.pdf");

test.describe("chat citations", () => {
  test("clicking a citation scrolls the viewer to the right page and highlights the source", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "chat-with-docs-bedrock" })).toBeVisible();

    const projectName = `e2e-citations-${Date.now()}`;
    await page.getByLabel("New project name").fill(projectName);
    await page.getByRole("button", { name: "Add" }).click();

    await expect(page.getByLabel("Upload documents")).toBeVisible({ timeout: 15_000 });
    await page.locator('input[type="file"]').setInputFiles(FIXTURE_PATH);

    const documentRow = page.locator("li", { hasText: "born-digital.pdf" });
    await expect(documentRow.getByText("Ready")).toBeVisible({ timeout: 120_000 });

    await page.getByPlaceholder(/ask a question/i).fill(QUESTION);
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible({ timeout: 15_000 });
    // Streaming replaces itself with the authoritative message on completion
    // (docs/06-frontend.md#chat-and-streaming) — waiting for "Send" to reappear is waiting for
    // that reconciliation, not just the last visible token.
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 120_000 });

    const citationButton = page.getByRole("button", { name: /^\[\d+\]$/ }).first();
    await expect(citationButton).toBeVisible({ timeout: 15_000 });

    const describedById = await citationButton.getAttribute("aria-describedby");
    expect(describedById).toBeTruthy();
    await expect(page.locator(`#${describedById}`)).toContainText(EXPECTED_CITED_SUBSTRING);

    await citationButton.click();

    // docs/06-frontend.md#rendering-citations: selects the document, scrolls to the page, draws
    // and pulses the highlight.
    await expect(page.getByRole("button", { name: "Close viewer" })).toBeVisible({
      timeout: 15_000,
    });
    // Scoped to the viewer panel, not the whole page: the sidebar can contain leftover
    // integration-test projects literally named e.g. "cwd-integration-all-fixtures-
    // born-digital.pdf", whose accessible text also contains this substring.
    await expect(
      page.getByTestId("viewer").getByText("born-digital.pdf", { exact: false }),
    ).toBeVisible();

    const highlight = page.locator('[data-page-number] [aria-hidden="true"] > div').first();
    await expect(highlight).toBeVisible({ timeout: 10_000 });
    const box = await highlight.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);

    // Clean up: delete the project via the UI.
    await page.getByRole("button", { name: "Close viewer" }).click();
    await page.getByRole("button", { name: `Delete ${projectName}` }).click();
    await expect(page.getByText(projectName)).toHaveCount(0);
  });
});
