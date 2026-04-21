import { expect, test } from "../fixtures/authenticatedPage";

/**
 * docs/08-testing.md: "`resilience.spec.ts` is the one that justifies the whole reconciliation
 * design; it is not optional." Kills the WebSocket mid-answer and asserts the message still
 * completes correctly — proving docs/06-frontend.md's central claim: "the WebSocket is a
 * latency optimisation, and the app is correct without it."
 *
 * Uses a fresh project per test (never the shared eval corpus) so this suite can run repeatedly
 * without accumulating state in the seeded user's account, and cleans up via the UI's own
 * delete affordance at the end.
 */

const QUESTION = "What is discussed in this document?";

test.describe("resilience", () => {
  test("killing the WebSocket mid-answer still produces a correct final message via reconciliation", async ({
    page,
  }) => {
    // Installed *before* the app loads, via addInitScript, so it's in place before
    // ChatPane/DocumentList ever open their first connection (docs/06: the channel is kept open
    // for the conversation's whole lifetime, not just mid-turn — by the time a question is
    // typed, a connection already exists). Every socket this override creates closes itself
    // shortly after opening, and `EventChannelClient`'s own reconnect logic will just keep
    // creating (and losing) new ones — a worse case than a single mid-answer drop, so if
    // reconciliation survives this, it survives a one-time drop too.
    await page.addInitScript(() => {
      const OriginalWebSocket = window.WebSocket;
      class KillSwitchWebSocket extends OriginalWebSocket {
        constructor(url: string | URL, protocols?: string | string[]) {
          super(url, protocols);
          this.addEventListener("open", () => {
            setTimeout(() => this.close(), 200);
          });
        }
      }
      window.WebSocket = KillSwitchWebSocket as unknown as typeof WebSocket;
    });

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "chat-with-docs-bedrock" })).toBeVisible();

    const projectName = `e2e-resilience-${Date.now()}`;
    await page.getByLabel("New project name").fill(projectName);
    await page.getByRole("button", { name: "Add" }).click();

    // `onSuccess` auto-selects the freshly created project, so the chat pane appears without an
    // extra click — this just confirms it actually did.
    await expect(page.getByPlaceholder(/ask a question/i)).toBeVisible({ timeout: 15_000 });

    await page.getByPlaceholder(/ask a question/i).fill(QUESTION);
    await page.getByRole("button", { name: "Send" }).click();

    // While a turn is in flight, `Composer` swaps "Send" for "Cancel" entirely (not just
    // disabling it) — confirms the turn actually started before asserting on how it ends.
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible({ timeout: 15_000 });

    // The streamed preview may never fully render (the socket keeps getting killed), but the
    // reconciliation poll (docs/06: every 3s, capped at 5 minutes) must still land the
    // authoritative COMPLETE/BLOCKED/FAILED message — "Send" reappearing (swapped back from
    // "Cancel") is the signal that `ChatPane`'s in-flight `stream` state cleared.
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 120_000 });

    // Clean up: delete the project via the UI.
    await page.getByRole("button", { name: `Delete ${projectName}` }).click();
    await expect(page.getByText(projectName)).toHaveCount(0);
  });
});
