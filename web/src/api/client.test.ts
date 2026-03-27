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
