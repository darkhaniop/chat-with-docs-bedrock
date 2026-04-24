import { afterEach, describe, expect, it, vi } from "vitest";

const getUser = vi.fn();

vi.mock("../auth/oidc", () => ({
  userManager: { getUser },
}));

/** A minimal, controllable stand-in for the browser `WebSocket` — this project hand-rolls the
 * AppSync Events wire protocol rather than pulling in a mocking library (`mock-socket` etc.),
 * matching the "dumb fake, not a simulator" philosophy docs/08-testing.md uses for the Python
 * fakes. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState: number = WebSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(
    public url: string,
    public protocols: string[],
  ) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = WebSocket.CLOSED;
    this.onclose?.();
  }

  open(): void {
    this.readyState = WebSocket.OPEN;
    this.onopen?.();
  }

  serverMessage(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  sentTypes(): string[] {
    return this.sent.map((s) => (JSON.parse(s) as { type: string }).type);
  }
}

function factory(): (url: string, protocols: string[]) => FakeWebSocket {
  return (url, protocols) => new FakeWebSocket(url, protocols);
}

function expectSocket(index: number): FakeWebSocket {
  const socket = FakeWebSocket.instances[index];
  if (socket === undefined) throw new Error(`expected a FakeWebSocket at index ${index}`);
  return socket;
}

async function importClient() {
  const { EventChannelClient } = await import("./channelClient");
  return EventChannelClient;
}

describe("EventChannelClient", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    getUser.mockReset();
    FakeWebSocket.instances = [];
  });

  it("connects with the Cognito-user-pool subprotocol and sends connection_init on open", async () => {
    getUser.mockResolvedValue({ id_token: "the-id-token" });
    const EventChannelClient = await importClient();
    const client = new EventChannelClient(
      "abc.appsync-realtime-api.us-east-1.amazonaws.com",
      "abc.appsync-api.us-east-1.amazonaws.com",
      factory() as never,
    );

    client.subscribe("/conversations/conv-1", () => {});
    await Promise.resolve();
    await Promise.resolve();

    const socket = expectSocket(0);
    expect(socket.url).toBe(
      "wss://abc.appsync-realtime-api.us-east-1.amazonaws.com/event/realtime",
    );
    expect(socket.protocols[0]).toBe("aws-appsync-event-ws");
    expect(socket.protocols[1]).toMatch(/^header-/);

    socket.open();
    expect(socket.sentTypes()).toEqual(["connection_init"]);
  });

  it("subscribes after connection_ack and delivers events to the listener", async () => {
    getUser.mockResolvedValue({ id_token: "the-id-token" });
    const EventChannelClient = await importClient();
    const client = new EventChannelClient(
      "realtime.example.com",
      "http.example.com",
      factory() as never,
    );

    const received: unknown[] = [];
    client.subscribe("/conversations/conv-1", (envelope) => received.push(envelope));
    await Promise.resolve();
    await Promise.resolve();

    const socket = expectSocket(0);
    socket.open();
    socket.serverMessage({ type: "connection_ack", connectionTimeoutMs: 300_000 });
    await Promise.resolve();
    await Promise.resolve();

    const subscribeMessage = JSON.parse(
      socket.sent.find((s) => (JSON.parse(s) as { type: string }).type === "subscribe") ?? "{}",
    ) as { id: string; channel: string };
    expect(subscribeMessage.channel).toBe("/conversations/conv-1");

    socket.serverMessage({ type: "subscribe_success", id: subscribeMessage.id });
    socket.serverMessage({
      type: "data",
      id: subscribeMessage.id,
      event: [JSON.stringify({ type: "message.delta", seq: 1, data: { text: "hi" } })],
    });

    expect(received).toEqual([{ type: "message.delta", seq: 1, data: { text: "hi" } }]);
  });

  it("skips a data-message event that parses to something other than an envelope object", async () => {
    // `JSON.parse` succeeding is not the same as "this is a well-formed ChannelEnvelope" — a
    // bare number or string is also valid JSON. Found worth pinning after investigating a
    // live "can't access property messageId" crash report: nothing before this fix verified
    // the parsed shape before handing it to a subscriber's listener.
    getUser.mockResolvedValue({ id_token: "the-id-token" });
    const EventChannelClient = await importClient();
    const client = new EventChannelClient(
      "realtime.example.com",
      "http.example.com",
      factory() as never,
    );

    const received: unknown[] = [];
    client.subscribe("/conversations/conv-1", (envelope) => received.push(envelope));
    await Promise.resolve();
    await Promise.resolve();

    const socket = expectSocket(0);
    socket.open();
    socket.serverMessage({ type: "connection_ack", connectionTimeoutMs: 300_000 });
    await Promise.resolve();
    await Promise.resolve();

    const subscribeMessage = JSON.parse(
      socket.sent.find((s) => (JSON.parse(s) as { type: string }).type === "subscribe") ?? "{}",
    ) as { id: string };
    socket.serverMessage({ type: "subscribe_success", id: subscribeMessage.id });

    socket.serverMessage({
      type: "data",
      id: subscribeMessage.id,
      event: [
        "5", // a bare number — valid JSON, not an envelope
        '"a string"', // a bare string — same problem
        "null",
        JSON.stringify({ noType: true, data: {} }), // object, but missing `type`
        JSON.stringify({ type: "message.delta", data: { text: "ok" } }), // well-formed
      ],
    });

    expect(received).toEqual([{ type: "message.delta", data: { text: "ok" } }]);
  });

  it("resubscribes to every active channel after a reconnect", async () => {
    getUser.mockResolvedValue({ id_token: "the-id-token" });
    const EventChannelClient = await importClient();
    const client = new EventChannelClient(
      "realtime.example.com",
      "http.example.com",
      factory() as never,
    );

    client.subscribe("/projects/proj-1", () => {});
    await Promise.resolve();
    await Promise.resolve();
    const first = expectSocket(0);
    first.open();
    first.serverMessage({ type: "connection_ack", connectionTimeoutMs: 300_000 });
    await Promise.resolve();
    await Promise.resolve();
    expect(first.sentTypes()).toContain("subscribe");

    vi.useFakeTimers();
    first.close(); // simulate the socket dropping
    await vi.advanceTimersByTimeAsync(2000);
    await Promise.resolve();

    expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2);
    const second = expectSocket(FakeWebSocket.instances.length - 1);
    second.open();
    second.serverMessage({ type: "connection_ack", connectionTimeoutMs: 300_000 });
    await vi.advanceTimersByTimeAsync(0);

    expect(second.sentTypes()).toContain("subscribe");
  });

  it("does not attempt to reconnect once every subscriber has unsubscribed", async () => {
    getUser.mockResolvedValue({ id_token: "the-id-token" });
    const EventChannelClient = await importClient();
    const client = new EventChannelClient(
      "realtime.example.com",
      "http.example.com",
      factory() as never,
    );

    const unsubscribe = client.subscribe("/projects/proj-1", () => {});
    await Promise.resolve();
    await Promise.resolve();
    const socket = expectSocket(0);
    socket.open();
    unsubscribe();

    vi.useFakeTimers();
    socket.close();
    await vi.advanceTimersByTimeAsync(35_000);

    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
