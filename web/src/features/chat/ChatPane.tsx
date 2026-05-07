import { useEffect, useState } from "react";
import {
  useActiveConversation,
  useCancelMessage,
  useMessages,
  usePostMessage,
} from "../../api/hooks/conversations";
import { ApiError } from "../../api/errors";
import type { Citation } from "../../api/types";
import {
  initialStreamState,
  streamReducer,
  TERMINAL_STATUSES,
  type StreamState,
} from "../../realtime/streamReducer";
import { useChannelSubscription } from "../../realtime/useChannel";
import { Composer } from "./Composer";
import { MessageText } from "./MessageText";

const STATUS_LABEL: Record<string, string> = {
  BLOCKED: "Blocked by content safety",
  FAILED: "Failed to generate a response",
  CANCELLED: "Cancelled",
};

const STREAM_STATUS_LABEL: Record<string, string> = {
  starting: "Sending…",
  retrieving: "Searching documents…",
  thinking: "Thinking…",
};

// docs/06-frontend.md#reconnection-and-reconciliation: "If a message is STREAMING in the
// fetched record but no events are arriving, the UI polls that conversation every 3s until it
// is terminal, capped at 5 minutes."
const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

export function ChatPane({
  projectId,
  onCitationClick,
}: {
  projectId: string;
  onCitationClick?: (citation: Citation) => void;
}) {
  const { conversation, isLoading, ensureConversation } = useActiveConversation(projectId);
  const conversationId = conversation?.conversationId ?? null;
  const { data: messages, refetch } = useMessages(conversationId);
  const postMessage = usePostMessage();
  const cancelMessage = useCancelMessage(conversationId ?? "");
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState<StreamState | null>(null);
  const [announcement, setAnnouncement] = useState("");

  // docs/06: "the client should subscribe to the channel before posting where possible" — kept
  // open for the conversation's whole lifetime rather than only during a turn, so it already is
  // by the time a post happens.
  const channel = conversationId !== null ? `/conversations/${conversationId}` : null;
  useChannelSubscription(channel, (envelope) => {
    setStream((prev) => (prev !== null ? streamReducer(prev, envelope) : prev));
  });

  // The streamed text/citations are a preview only — once the turn reaches a terminal state,
  // `GET .../messages` is the authoritative record (docs/06-frontend.md#chat-and-streaming).
  useEffect(() => {
    if (stream !== null && TERMINAL_STATUSES.has(stream.status)) {
      const finalStatus = stream.status;
      void refetch().then(() => {
        setStream(null);
        // docs/06-frontend.md#accessibility-and-polish: "Streaming text uses aria-live='polite'
        // ... announced on completion rather than per delta" — the visible preview above
        // updates on every delta with no live region at all (that would spam a screen reader),
        // and this hidden region announces exactly once, only when a turn actually finishes.
        setAnnouncement(
          finalStatus === "done"
            ? "Assistant finished responding."
            : (STATUS_LABEL[finalStatus.toUpperCase()] ?? "Assistant response ended."),
        );
      });
    }
  }, [stream, refetch]);

  // docs/06-frontend.md#reconnection-and-reconciliation: "If a message is STREAMING in the
  // fetched record but no events are arriving, the UI polls." This deliberately does *not* gate
  // on `isConnected()` (the raw WebSocket's open/closed state) — found live that a WebSocket can
  // be fully OPEN (connection_ack received) while a specific `subscribe` for the conversation
  // channel silently never gets through (e.g. an expired id token on that one message, or any
  // other reason the server-side subscription doesn't end up delivering events), and neither
  // `subscribe_error` nor a plain "no data ever arrives" has any client-side detection today
  // (`channelClient.ts`'s `subscribe_error`/`broadcast_error` cases are still no-ops). Polling
  // unconditionally while a turn is in flight is a strictly more robust backstop: it costs a
  // little redundant network chatter on the common case where the channel *is* working (the
  // channel-driven terminal transition above almost always wins the race and clears `stream`
  // first), but it means "the channel isn't actually delivering, for whatever reason" can never
  // leave the composer stuck showing "Cancel" forever the way `isConnected()` alone allowed.
  useEffect(() => {
    if (stream === null || TERMINAL_STATUSES.has(stream.status)) return;
    const messageId = stream.messageId;
    const start = Date.now();
    const interval = setInterval(() => {
      if (Date.now() - start > POLL_TIMEOUT_MS) {
        clearInterval(interval);
        return;
      }
      // A refetch alone only refreshes the cache — nothing else inspects the result to notice
      // the turn is actually done, and `stream` (and therefore the composer's disabled/Cancel
      // state) would stay stuck forever otherwise. Found live via `e2e/tests/resilience.spec.ts`
      // for the disconnected case, and via a real deploy for the "connected but not delivering"
      // case above.
      void refetch().then((result) => {
        const found = result.data?.items.find((m) => m?.messageId === messageId);
        if (found !== undefined && found.status !== "STREAMING") {
          setStream(null);
        }
      });
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-armed on status change only
  }, [stream?.messageId, stream?.status, refetch]);

  const handleSend = async (text: string) => {
    setError(null);
    try {
      const active = await ensureConversation();
      const result = await postMessage.mutateAsync({ conversationId: active.conversationId, text });
      setStream(initialStreamState(result.assistantMessageId));
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Something went wrong sending that message.",
      );
    }
  };

  const isBusy = stream !== null && !TERMINAL_STATUSES.has(stream.status);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {isLoading && <p className="text-sm text-slate-500">Loading conversation…</p>}
        {!isLoading &&
          (messages === undefined || messages.items.length === 0) &&
          stream === null && (
            <p className="text-sm text-slate-500">Ask a question about this project's documents.</p>
          )}
        <ul className="flex flex-col gap-3">
          {messages?.items
            .filter((message) => message != null)
            .map((message) => (
              <li
                key={message.messageId}
                className={
                  message.role === "user"
                    ? "self-end rounded-lg bg-slate-900 px-3 py-2 text-sm text-white"
                    : "rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900"
                }
              >
                {message.status in STATUS_LABEL ? (
                  <span className="text-red-700">
                    {STATUS_LABEL[message.status]}
                    {message.text ? `: ${message.text}` : ""}
                  </span>
                ) : (
                  <MessageText
                    text={message.text}
                    citations={message.citations}
                    onCitationClick={onCitationClick}
                  />
                )}
              </li>
            ))}
          {isBusy && stream !== null && (
            <li className="rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900">
              {stream.text.length > 0 ? (
                <MessageText
                  text={stream.text}
                  citations={stream.citations}
                  onCitationClick={onCitationClick}
                />
              ) : (
                <span className="text-slate-500">
                  {STREAM_STATUS_LABEL[stream.status] ?? "Working…"}
                </span>
              )}
            </li>
          )}
        </ul>
        {error !== null && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </div>
      <Composer
        disabled={postMessage.isPending || isBusy}
        onSend={(text) => void handleSend(text)}
        onCancel={
          isBusy && stream !== null ? () => cancelMessage.mutate(stream.messageId) : undefined
        }
      />
    </div>
  );
}
