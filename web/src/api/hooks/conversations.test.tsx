import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useActiveConversation } from "./conversations";
import { apiJson } from "../client";
import type { Conversation } from "../types";

vi.mock("../client", () => ({ apiJson: vi.fn() }));
const mockApiJson = vi.mocked(apiJson);

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function conversation(id: string): Conversation {
  return {
    conversationId: id,
    projectId: "proj-1",
    title: "",
    pinnedDocumentIds: [],
    messageCount: 0,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };
}

function isPost(init: unknown): boolean {
  return (init as RequestInit | undefined)?.method === "POST";
}

/**
 * A conversation created lazily on first Send left `ChatPane`'s channel subscription (keyed by
 * `conversationId`, which doesn't exist until a conversation does) no time to complete before the
 * answering worker started publishing, so the first turn in any new conversation reliably missed
 * every streamed delta (found live: "never observed an actual streaming behavior").
 * `useActiveConversation` now creates a conversation eagerly as soon as a project is
 * selected and none exists, rather than waiting for `ensureConversation()` to be called from
 * `handleSend`.
 */
describe("useActiveConversation", () => {
  beforeEach(() => {
    mockApiJson.mockReset();
  });

  it("eagerly creates a conversation when the project has none yet", async () => {
    mockApiJson.mockImplementation((path: unknown, init?: unknown) => {
      if (isPost(init)) return Promise.resolve(conversation("conv-1"));
      return Promise.resolve({ items: [], nextCursor: null });
    });

    renderHook(() => useActiveConversation("proj-1"), { wrapper });

    await waitFor(() => {
      expect(mockApiJson).toHaveBeenCalledWith(
        "/projects/proj-1/conversations",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("does not create a conversation when one already exists", async () => {
    mockApiJson.mockResolvedValue({ items: [conversation("conv-existing")], nextCursor: null });

    const { result } = renderHook(() => useActiveConversation("proj-1"), { wrapper });

    await waitFor(() => {
      expect(result.current.conversation?.conversationId).toBe("conv-existing");
    });

    expect(mockApiJson.mock.calls.some(([, init]) => isPost(init))).toBe(false);
  });

  it("fires the eager create at most once per project even across re-renders", async () => {
    mockApiJson.mockImplementation((path: unknown, init?: unknown) => {
      if (isPost(init)) return Promise.resolve(conversation("conv-1"));
      // Keeps returning an empty list even after the create "succeeds" server-side, simulating
      // a slow refetch — the guard must be a ref, not just "no conversation exists yet".
      return Promise.resolve({ items: [], nextCursor: null });
    });

    const { rerender } = renderHook(() => useActiveConversation("proj-1"), { wrapper });
    rerender();
    rerender();

    await waitFor(() => {
      expect(mockApiJson.mock.calls.filter(([, init]) => isPost(init))).toHaveLength(1);
    });
  });

  it("does not create a conversation before the project's conversation list has loaded", () => {
    mockApiJson.mockReturnValue(new Promise(() => {})); // never resolves
    renderHook(() => useActiveConversation("proj-1"), { wrapper });
    expect(mockApiJson.mock.calls.some(([, init]) => isPost(init))).toBe(false);
  });
});
