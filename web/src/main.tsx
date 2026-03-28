import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AppProviders } from "./app/providers";
import { AuthProvider } from "./auth/useAuth";
import "./index.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("root element not found");
}

createRoot(container).render(
  <StrictMode>
    <AppProviders>
      <AuthProvider>
        <App />
      </AuthProvider>
    </AppProviders>
  </StrictMode>,
);
