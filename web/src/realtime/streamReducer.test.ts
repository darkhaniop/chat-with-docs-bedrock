import { describe, expect, it } from "vitest";
import { initialStreamState, streamReducer, type ChannelEnvelope } from "./streamReducer";

function envelope(type: string, seq: number, data: Record<string, unknown> = {}): ChannelEnvelope {
  return { type, seq, data: { messageId: "msg-1", ...data } };
}

describe("streamReducer", () => {
  it("orders deltas by appending in arrival order", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(state, envelope("message.started", 1));
    state = streamReducer(state, envelope("message.delta", 2, { text: "Hello, " }));
    state = streamReducer(state, envelope("message.delta", 3, { text: "world." }));

    expect(state.text).toBe("Hello, world.");
    expect(state.status).toBe("streaming");
    expect(state.lastSeq).toBe(3);
    expect(state.gapDetected).toBe(false);
  });

  it("detects a gap when seq skips ahead, without reordering or backfilling", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(state, envelope("message.started", 1));
    state = streamReducer(state, envelope("message.delta", 2, { text: "a" }));
    state = streamReducer(state, envelope("message.delta", 5, { text: "b" }));

    expect(state.text).toBe("ab");
    expect(state.gapDetected).toBe(true);
    expect(state.lastSeq).toBe(5);
  });

  it("appends a citation that arrives before the text it anchors to", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(
      state,
      envelope("message.citation", 1, { citation: { citationId: "c0", pageNumber: 1 } }),
    );
    expect(state.citations).toHaveLength(1);
    expect(state.text).toBe("");

    state = streamReducer(state, envelope("message.delta", 2, { text: "The answer." }));
    expect(state.text).toBe("The answer.");
    expect(state.citations).toHaveLength(1);
  });

  it("records retrieval sources", () => {
    const sources = [{ documentId: "doc-1", filename: "a.pdf", pageNumber: 1, score: 0.9 }];
    let state = initialStreamState("msg-1");
    state = streamReducer(state, envelope("message.retrieval", 1, { sources }));
    expect(state.sources).toEqual(sources);
  });

  it("reaches the done status on message.completed", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(state, envelope("message.delta", 1, { text: "hi" }));
    state = streamReducer(state, envelope("message.completed", 2, {}));
    expect(state.status).toBe("done");
  });

  it("reaches the blocked status with a reason", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(
      state,
      envelope("message.blocked", 1, { reason: "guardrail_intervened" }),
    );
    expect(state.status).toBe("blocked");
    expect(state.failureMessage).toBe("guardrail_intervened");
  });

  it("reaches the failed status with a message", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(
      state,
      envelope("message.failed", 1, { code: "TIMEOUT", message: "too slow" }),
    );
    expect(state.status).toBe("failed");
    expect(state.failureMessage).toBe("too slow");
  });

  it("ignores events for a different messageId", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(state, {
      type: "message.delta",
      seq: 1,
      data: { messageId: "some-other-message", text: "nope" },
    });
    expect(state.text).toBe("");
    expect(state.lastSeq).toBe(0);
  });

  it("degrades gracefully on an unrecognised event type", () => {
    let state = initialStreamState("msg-1");
    state = streamReducer(state, envelope("message.some_future_type", 1, { whatever: true }));
    expect(state.lastSeq).toBe(1);
    expect(state.status).toBe("starting");
  });

  it("does not crash when data is missing or not an object", () => {
    // `ChannelEnvelope.data` is typed as `Record<string, unknown>`, but that's a compile-time
    // guarantee only — nothing upstream verifies it against a malformed/unexpected wire payload
    // at runtime. docs/04's "an unparseable event degrades gracefully — never a crash" applies
    // here too, not just to a JSON.parse failure.
    let state = initialStreamState("msg-1");
    state = streamReducer(state, {
      type: "message.delta",
      seq: 1,
      data: undefined as unknown as Record<string, unknown>,
    });
    expect(state.text).toBe("");
    expect(state.status).toBe("streaming");

    state = streamReducer(state, {
      type: "message.delta",
      seq: 2,
      data: null as unknown as Record<string, unknown>,
    });
    expect(state.text).toBe("");
  });
});
