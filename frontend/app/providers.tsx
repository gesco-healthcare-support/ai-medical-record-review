"use client";

import { useState, type ReactNode } from "react";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api";

/** A 401 anywhere means the session is gone -> send the user to /login (unless already there,
 *  which would loop). Centralized so every query/mutation inherits the behavior. */
function redirectOnAuthError(error: unknown) {
  if (
    error instanceof ApiError &&
    error.status === 401 &&
    typeof window !== "undefined" &&
    !window.location.pathname.startsWith("/login")
  ) {
    window.location.assign("/login");
  }
}

/**
 * Client-side providers shared by every route: the TanStack Query cache (server state, polling)
 * and the Radix tooltip provider. Instantiated once via useState so the client survives
 * re-renders but is per-request on the server.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: redirectOnAuthError }),
        mutationCache: new MutationCache({ onError: redirectOnAuthError }),
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: (count, error) =>
              !(error instanceof ApiError && [401, 403, 404].includes(error.status)) && count < 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
    </QueryClientProvider>
  );
}
