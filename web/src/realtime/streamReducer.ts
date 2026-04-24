import type { Citation } from "../api/types";

/** docs/06-frontend.md#chat-and-streaming: the in-flight assistant message, reconstructed from
 * conversation-channel events. Never persisted as truth — `message.completed` triggers a `GET`
 * that replaces this reconstruction with the authoritative record. */
export interface Source {
  documentId: string;
  filename: string;
  pageNumber: number;
  score: number | null;
}

export type StreamStatus =
  "starting" | "retrieving" | "thinking" | "streaming" | "done" | "blocked" | "failed";

export interface StreamState {
  messageId: string;
  status: StreamStatus;
  text: string;
  citations: Citation[];
  sources: Source[];
  lastSeq: number;
  gapDetected: boolean;
  failureMessage: string | null;
}

export function initialStreamState(messageId: string): StreamState {
  return {
    messageId,
    status: "starting",
    text: "",
    citations: [],
    sources: [],
    lastSeq: 0,
    gapDetected: false,
    failureMessage: null,
  };
}

export interface ChannelEnvelope {
  type: string;
  seq: number;
  data: Record<string, unknown>;
}

/**
 * Pure reducer over conversation-channel events (docs/05-api-contracts.md#appsync-events'
 * conversation-channel table, docs/06-frontend.md#chat-and-streaming's rules). Events for a
 * different `messageId` than this state tracks are ignored — a channel briefly carries the
 * outgoing turn's events only, but a stale subscriber from a just-completed turn could still
 * receive a straggler.
 */
export function streamReducer(state: StreamState, envelope: ChannelEnvelope): StreamState {
  // `channelClient.ts` already validates the parsed envelope has both `type` and `data` keys
  // before ever calling this reducer, but `data`'s own shape is only asserted at the type
  // level (TS), never checked at runtime — this is the one extra guard that keeps a malformed
  // `data` value from crashing the reducer outright, matching docs/04's "an unparseable event
  // degrades gracefully — never a crash."
  const data = typeof envelope.data === "object" && envelope.data !== null ? envelope.data : {};
  if (typeof data.messageId === "string" && data.messageId !== state.messageId) {
    return state;
  }

  // docs/06: "If seq !== lastSeq + 1, set gapDetected — do not attempt to reorder or backfill."
  const gapDetected = state.lastSeq !== 0 && envelope.seq !== state.lastSeq + 1;
  const base: StreamState = { ...state, lastSeq: envelope.seq, gapDetected };

  switch (envelope.type) {
    case "message.started":
      return { ...base, status: "retrieving" };

    case "message.rewritten":
      return base;

    case "message.retrieval":
      return {
        ...base,
        sources: Array.isArray(data.sources) ? (data.sources as Source[]) : base.sources,
      };

    case "message.thinking":
      return { ...base, status: "thinking" };

    case "message.delta":
      return {
        ...base,
        status: "streaming",
        text: base.text + (typeof data.text === "string" ? data.text : ""),
      };

    case "message.citation":
      // docs/04: citations may arrive before the text they anchor to — they render in the
      // sources strip immediately and inline once `spanStart <= text.length`, which is
      // `MessageText`'s job at render time, not this reducer's.
      return {
        ...base,
        citations: data.citation ? [...base.citations, data.citation as Citation] : base.citations,
      };

    case "message.completed":
      return { ...base, status: "done" };

    case "message.blocked":
      return {
        ...base,
        status: "blocked",
        failureMessage: typeof data.reason === "string" ? data.reason : null,
      };

    case "message.failed":
      return {
        ...base,
        status: "failed",
        failureMessage: typeof data.message === "string" ? data.message : null,
      };

    default:
      // docs/04: an unrecognised event type degrades gracefully — never a crash.
      return base;
  }
}
