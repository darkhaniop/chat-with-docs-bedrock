import { userManager } from "../auth/oidc";
import type { ChannelEnvelope } from "./streamReducer";

/**
 * AppSync Events' WebSocket subprotocol
 * (https://docs.aws.amazon.com/appsync/latest/eventapi/event-api-websocket-protocol.html):
 * connect with `Sec-WebSocket-Protocol: aws-appsync-event-ws, header-<base64url>`, where the
 * `header-` value is `{Authorization: <id token>, host: <events HTTP domain>}` for Cognito User
 * Pool auth — confirmed against AWS's own docs page (not guessed), since this is the one place
 * getting the wire format wrong means "never connects" with no useful error.
 */
const PROTOCOL_NAME = "aws-appsync-event-ws";

function base64UrlEncode(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type Listener = (envelope: ChannelEnvelope) => void;

interface Subscription {
  channel: string;
  listener: Listener;
  acknowledged: boolean;
}

type WebSocketFactory = (url: string, protocols: string[]) => WebSocket;

const DEFAULT_FACTORY: WebSocketFactory = (url, protocols) => new WebSocket(url, protocols);

// docs/06-frontend.md#reconnection-and-reconciliation: "reconnects with exponential backoff
// (1s -> 30s, jittered)".
const BACKOFF_MIN_MS = 1000;
const BACKOFF_MAX_MS = 30_000;

/**
 * One WebSocket connection to the AppSync Events realtime endpoint, shared across every
 * `subscribe()` call in the app (docs/06-frontend.md#chat-and-streaming /
 * #reconnection-and-reconciliation). Connects lazily on the first subscribe, reconnects with
 * backoff on drop, and resubscribes to every still-active channel on reconnect. The app is
 * correct without this working at all — reconciliation (a `GET` on `message.completed`, or a
 * poll fallback) is what actually makes a turn's result land; this is a latency optimisation.
 */
export class EventChannelClient {
  private socket: WebSocket | null = null;
  private connecting = false;
  private subscriptions = new Map<string, Subscription>();
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly realtimeDomain: string,
    private readonly httpDomain: string,
    private readonly webSocketFactory: WebSocketFactory = DEFAULT_FACTORY,
  ) {}

  subscribe(channel: string, listener: Listener): () => void {
    const id = crypto.randomUUID();
    this.subscriptions.set(id, { channel, listener, acknowledged: false });
    this.ensureConnected();
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.sendSubscribe(id);
    }
    return () => {
      const sub = this.subscriptions.get(id);
      this.subscriptions.delete(id);
      if (sub?.acknowledged && this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ type: "unsubscribe", id }));
      }
    };
  }

  private async ensureConnected(): Promise<void> {
    if (this.socket !== null || this.connecting) return;
    this.connecting = true;
    try {
      const user = await userManager.getUser();
      const idToken = user?.id_token;
      if (idToken === undefined) {
        return;
      }
      const header = base64UrlEncode(
        JSON.stringify({ Authorization: idToken, host: this.httpDomain }),
      );
      const socket = this.webSocketFactory(`wss://${this.realtimeDomain}/event/realtime`, [
        PROTOCOL_NAME,
        `header-${header}`,
      ]);
      socket.onopen = () => {
        socket.send(JSON.stringify({ type: "connection_init" }));
      };
      socket.onmessage = (event) => this.handleMessage(event);
      socket.onclose = () => this.handleDisconnect();
      socket.onerror = () => socket.close();
      this.socket = socket;
    } catch {
      // The app is correct without a channel at all (docs/06: "a latency optimisation") — a
      // synchronous throw from constructing the socket itself (e.g. a malformed subprotocol
      // string) must not become an unhandled promise rejection. `handleDisconnect`'s own retry
      // isn't reachable here since `this.socket` was never assigned; the next `subscribe()`
      // call (or reconnect timer, if one is already pending) tries again.
    } finally {
      this.connecting = false;
    }
  }

  private handleMessage(event: MessageEvent<string>): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(event.data) as Record<string, unknown>;
    } catch {
      return;
    }

    switch (message.type) {
      case "connection_ack":
        this.reconnectAttempt = 0;
        for (const id of this.subscriptions.keys()) this.sendSubscribe(id);
        break;
      case "subscribe_success": {
        const sub = this.subscriptions.get(message.id as string);
        if (sub) sub.acknowledged = true;
        break;
      }
      case "subscribe_error":
      case "broadcast_error":
        // A subscribe can fail server-side (e.g. an expired id token on that one message)
        // while the WebSocket connection itself stays fully open — found live as a stuck
        // "Sending…" bubble with no client-side signal at all that anything had gone wrong.
        // There's no good automatic recovery here yet (no silent token renew — docs/06's own
        // gap note), so this is a diagnostic aid, not a fix: `ChatPane`'s reconciliation poll
        // (which no longer depends on the raw socket's connected/disconnected state) is what
        // actually recovers the turn regardless of what this branch does.
        console.warn(`AppSync Events ${message.type}`, message);
        break;
      case "data": {
        const sub = this.subscriptions.get(message.id as string);
        if (!sub) return;
        const rawEvents = (message.event as string[] | undefined) ?? [];
        for (const raw of rawEvents) {
          try {
            const parsed: unknown = JSON.parse(raw);
            // Defensive per docs/04: an unparseable/malformed event degrades to "missed one
            // delta", never a crash — reconciliation covers the gap. `JSON.parse` succeeding
            // is not enough on its own: a bare number or string is also valid JSON, and would
            // otherwise reach `streamReducer` as a non-object `envelope`, crashing on
            // `envelope.data.messageId`.
            if (
              typeof parsed === "object" &&
              parsed !== null &&
              "type" in parsed &&
              "data" in parsed
            ) {
              sub.listener(parsed as ChannelEnvelope);
            }
          } catch {
            // JSON.parse itself failed — same "skip this one event" handling as above.
          }
        }
        break;
      }
      // "ka" (keep-alive), "unsubscribe_success"/"error": nothing this client needs to act on
      // beyond staying connected.
      default:
        break;
    }
  }

  private sendSubscribe(id: string): void {
    const sub = this.subscriptions.get(id);
    if (!sub || this.socket?.readyState !== WebSocket.OPEN) return;
    void userManager.getUser().then((user) => {
      const idToken = user?.id_token;
      if (idToken === undefined || this.socket?.readyState !== WebSocket.OPEN) return;
      this.socket.send(
        JSON.stringify({
          type: "subscribe",
          id,
          channel: sub.channel,
          authorization: { Authorization: idToken, host: this.httpDomain },
        }),
      );
    });
  }

  private handleDisconnect(): void {
    this.socket = null;
    for (const sub of this.subscriptions.values()) sub.acknowledged = false;
    if (this.subscriptions.size === 0) return;

    const delay = Math.min(BACKOFF_MIN_MS * 2 ** this.reconnectAttempt, BACKOFF_MAX_MS);
    const jittered = delay / 2 + Math.random() * (delay / 2);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => void this.ensureConnected(), jittered);
  }

  /** For tests and explicit teardown (e.g. sign-out) — not used by ordinary unmounts, since the
   * client is a module-level singleton shared across the app's lifetime. */
  disconnect(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.subscriptions.clear();
    this.socket?.close();
    this.socket = null;
  }
}

export const eventChannelClient = new EventChannelClient(
  import.meta.env.VITE_EVENTS_REALTIME_DOMAIN,
  import.meta.env.VITE_EVENTS_HTTP_DOMAIN,
);
