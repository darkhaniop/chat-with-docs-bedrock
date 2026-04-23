import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

/**
 * docs/06-frontend.md#accessibility-and-polish: "Dark mode via Tailwind's class strategy,
 * following the system preference by default." `index.css`'s `@custom-variant dark` opts
 * Tailwind v4 back into the class strategy; this is the one place that decides when `.dark` is
 * actually applied, so no other component needs to know dark mode exists at all.
 */
function useSystemDarkMode() {
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = (matches: boolean) => {
      document.documentElement.classList.toggle("dark", matches);
    };
    apply(media.matches);
    const listener = (event: MediaQueryListEvent) => apply(event.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, []);
}

/** docs/06-frontend.md#layout: server state is TanStack Query's job everywhere; this is the
 * one place the client is constructed. Auth/events providers join this file as they arrive. */
export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());
  useSystemDarkMode();
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
