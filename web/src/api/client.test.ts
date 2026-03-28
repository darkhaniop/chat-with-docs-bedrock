import { afterEach, describe, expect, it, vi } from "vitest";

const signinRedirect = vi.fn().mockResolvedValue(undefined);
const getUser = vi.fn();

vi.mock("../auth/oidc", () => ({
  apiBase: "https://api.test.example.com",
  userManager: { getUser, signinRedirect },
}));

describe("apiFetch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    signinRedirect.mockClear();
    getUser.mockReset();
  });

  it("attaches the Cognito ID token as a bearer header when a valid user exists", async () => {
    getUser.mockResolvedValue({ expired: false, id_token: "the-id-token" });
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const { apiFetch } = await import("./client");
    await apiFetch("/echo");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.test.example.com/echo");
    expect((init.headers as Headers).get("Authorization")).toBe("Bearer the-id-token");
  });

  it("omits the Authorization header when there is no valid user", async () => {
    getUser.mockResolvedValue(null);
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const { apiFetch } = await import("./client");
    await apiFetch("/echo");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).has("Authorization")).toBe(false);
  });

  it("redirects to sign-in on a 401 and does not retry in a loop", async () => {
    getUser.mockResolvedValue({ expired: false, id_token: "stale-token" });
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const { apiFetch } = await import("./client");
    const response = await apiFetch("/echo");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(signinRedirect).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(401);
  });
});

describe("apiJson", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    getUser.mockReset();
  });

  it("returns the parsed JSON body on success", async () => {
    getUser.mockResolvedValue(null);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 })),
    );

    const { apiJson } = await import("./client");
    await expect(apiJson<{ ok: boolean }>("/projects")).resolves.toEqual({ ok: true });
  });

  it("sets Content-Type when a body is given", async () => {
    getUser.mockResolvedValue(null);
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    const { apiJson } = await import("./client");
    await apiJson("/projects", { method: "POST", body: JSON.stringify({ name: "x" }) });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).get("Content-Type")).toBe("application/json");
  });

  it("throws ApiError with the code/message from the error envelope on a non-2xx response", async () => {
    getUser.mockResolvedValue(null);
    const body = { error: { code: "VALIDATION_ERROR", message: "`name` is required." } };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response(JSON.stringify(body), { status: 400 })),
        ),
    );

    const { apiJson } = await import("./client");
    const { ApiError } = await import("./errors");

    const error = await apiJson("/projects", { method: "POST" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 400,
      code: "VALIDATION_ERROR",
      message: "`name` is required.",
    });
  });

  it("returns undefined for a 204 response", async () => {
    getUser.mockResolvedValue(null);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    const { apiJson } = await import("./client");
    await expect(apiJson("/conversations/1")).resolves.toBeUndefined();
  });
});
