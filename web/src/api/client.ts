import { apiBase, userManager } from "../auth/oidc";

/**
 * docs/06-frontend.md#authentication: on 401, redirect to login. It never retries a 401 in a
 * loop. The documented "one silent renew, then retry" step needs the hidden-iframe callback
 * wiring that `auth/oidc.ts` defers (see its comment) — until then this goes straight to a full
 * redirect, which is correct but interrupts the user rather than renewing invisibly.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: await authHeaders(init.headers),
  });
  if (response.status === 401) {
    await userManager.signinRedirect();
  }
  return response;
}

async function authHeaders(base?: HeadersInit): Promise<Headers> {
  const headers = new Headers(base);
  const user = await userManager.getUser();
  if (user !== null && !user.expired) {
    // docs/05-api-contracts.md#conventions: bearer the Cognito ID token, not the access token.
    headers.set("Authorization", `Bearer ${user.id_token}`);
  }
  return headers;
}
