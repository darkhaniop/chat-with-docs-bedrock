/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    env: {
      // Fixed test-only values — real ones are injected at build time by
      // scripts/build-web.sh (docs/06-frontend.md#build-and-deploy).
      VITE_COGNITO_AUTHORITY: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
      VITE_COGNITO_DOMAIN: "cwd-dev-000000000000.auth.us-east-1.amazoncognito.com",
      VITE_COGNITO_CLIENT_ID: "test-client-id",
      VITE_API_BASE: "https://api.test.example.com",
    },
  },
});
