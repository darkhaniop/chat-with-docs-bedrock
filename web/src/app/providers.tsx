import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

/** docs/06-frontend.md#layout: server state is TanStack Query's job everywhere; this is the
 * one place the client is constructed. Auth/events providers join this file as they arrive. */
export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
