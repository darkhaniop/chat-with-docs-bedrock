import { UserManager, WebStorageStateStore } from "oidc-client-ts";

// docs/06-frontend.md#authentication, docs/07-security.md#token-handling: Cognito Managed
// Login, authorization code flow with PKCE, public client, no secret. Tokens live in memory
// (oidc-client-ts's in-memory user object); only the refresh token is persisted, and
// deliberately in sessionStorage rather than localStorage so closing the tab ends the session.
function requiredEnv(name: keyof ImportMetaEnv): string {
  const value = import.meta.env[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Missing required env var ${name} — see web/.env.example`);
  }
  return value;
}

export const cognitoDomain = requiredEnv("VITE_COGNITO_DOMAIN");
export const cognitoClientId = requiredEnv("VITE_COGNITO_CLIENT_ID");
export const apiBase = requiredEnv("VITE_API_BASE");

const redirectUri = new URL("/callback", window.location.origin).toString();
const postLogoutUri = new URL("/", window.location.origin).toString();

// Proactive silent renew via a hidden iframe (docs/06-frontend.md#authentication) needs a
// dedicated callback page verified against a real Managed Login domain in a browser — deferred
// until there's a deployed `dev` stack to test it against (docs/10-roadmap.md's Phase 1
// progress log). For now, `api/client.ts` covers the load-bearing case: a 401 triggers a full
// `signinRedirect()`, which is correct but not silent.
export const userManager = new UserManager({
  authority: requiredEnv("VITE_COGNITO_AUTHORITY"),
  client_id: cognitoClientId,
  redirect_uri: redirectUri,
  response_type: "code",
  scope: "openid email profile",
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
});

/**
 * Cognito's OIDC discovery document has no `end_session_endpoint` (it does not implement
 * RP-initiated logout), so sign-out is a direct redirect to the Managed Login domain's own
 * `/logout` endpoint rather than `userManager.signoutRedirect()`.
 */
export function signOutRedirect(): void {
  const url = new URL(`https://${cognitoDomain}/logout`);
  url.searchParams.set("client_id", cognitoClientId);
  url.searchParams.set("logout_uri", postLogoutUri);
  window.location.href = url.toString();
}
