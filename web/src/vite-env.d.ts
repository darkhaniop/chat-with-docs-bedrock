/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Injected at build time from CDK outputs by scripts/build-web.sh
  // (docs/06-frontend.md#build-and-deploy) — never fetched at runtime.
  readonly VITE_COGNITO_AUTHORITY: string;
  readonly VITE_COGNITO_DOMAIN: string;
  readonly VITE_COGNITO_CLIENT_ID: string;
  readonly VITE_API_BASE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
