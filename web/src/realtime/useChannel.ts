import { useEffect, useInsertionEffect, useRef } from "react";
import { eventChannelClient } from "./channelClient";
import type { ChannelEnvelope } from "./streamReducer";

/**
 * Subscribes to `channel` for the lifetime of the component (or until `channel` changes/becomes
 * `null`), forwarding every event to `onEvent` (docs/06-frontend.md#chat-and-streaming). The
 * callback ref indirection means callers don't need to memoize `onEvent` themselves —
 * `useInsertionEffect` (not a plain assignment during render) is what React's own hooks lint
 * rule requires for writing a ref that render itself doesn't read.
 */
export function useChannelSubscription(
  channel: string | null,
  onEvent: (envelope: ChannelEnvelope) => void,
): void {
  const onEventRef = useRef(onEvent);
  useInsertionEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    if (channel === null) return;
    return eventChannelClient.subscribe(channel, (envelope) => onEventRef.current(envelope));
  }, [channel]);
}

/** Tracks the shared channel client's connect/disconnect state — used only to decide whether a
 * polling fallback is needed (docs/06: "If a message is STREAMING ... but no events are
 * arriving, the UI polls"), never to gate correctness. */
export function useChannelConnected(): () => boolean {
  const connectedRef = useRef(eventChannelClient.isConnected);

  useEffect(() => {
    return eventChannelClient.addStatusListener((connected) => {
      connectedRef.current = connected;
    });
  }, []);

  return () => connectedRef.current;
}
