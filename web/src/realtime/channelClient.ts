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
  private statusListeners = new Set<(connected: boolean) => void>();

  constructor(
    private readonly realtimeDomain: string,
    private readonly httpDomain: string,
    private readonly webSocketFactory: WebSocketFactory = DEFAULT_FACTORY,
  ) {}

  /** Notified on every connect/disconnect — `DocumentList`/`ChatPane` use this only to decide
   * whether to fall back to polling, never to gate correctness. A set, not a single slot, since
   * multiple components (chat pane, document list) each track connectivity independently. */
  addStatusListener(listener: (connected: boolean) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

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

  get isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  private async ensureConnected(): Promise<void> {
    if (this.socket !== null || this.connecting) return;
    this.connecting = true;
    try {
      const user = await userManager.getUser();
      const idToken = user?.id_token;
      if (idToken === undefined) {
        this.connecting = false;
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
        for (const l of this.statusListeners) l(true);
        for (const id of this.subscriptions.keys()) this.sendSubscribe(id);
        break;
      case "subscribe_success": {
        const sub = this.subscriptions.get(message.id as string);
        if (sub) sub.acknowledged = true;
        break;
      }
      case "data": {
        const sub = this.subscriptions.get(message.id as string);
        if (!sub) return;
        const rawEvents = (message.event as string[] | undefined) ?? [];
        for (const raw of rawEvents) {
          try {
            sub.listener(JSON.parse(raw) as ChannelEnvelope);
          } catch {
            // Defensive per docs/04: an unparseable event degrades to "missed one delta",
            // never a crash — reconciliation covers the gap.
          }
        }
        break;
      }
      // "ka" (keep-alive), "subscribe_error", "unsubscribe_success"/"error": nothing this
      // client needs to act on beyond staying connected.
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
    for (const l of this.statusListeners) l(false);
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
