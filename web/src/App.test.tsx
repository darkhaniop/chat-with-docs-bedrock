import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

describe("App", () => {
  it("renders the app shell", () => {
    render(<App />);
    expect(screen.getByText("chat-with-docs")).toBeInTheDocument();
  });
});
