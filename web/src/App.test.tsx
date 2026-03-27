import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { AuthProvider } from "./auth/useAuth";

describe("App", () => {
  it("renders a sign-in screen when unauthenticated", async () => {
    render(
      <AuthProvider>
        <App />
      </AuthProvider>,
    );

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});
