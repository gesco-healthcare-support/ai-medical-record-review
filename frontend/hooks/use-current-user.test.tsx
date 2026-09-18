/**
 * The signed-in user.
 *
 * The no-retry setting is the part worth pinning: a 401 here means the session is gone, and
 * retrying it just delays the redirect to /login while the app renders a signed-out screen as
 * though it were still loading.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiFetch = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import { ApiError } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

function wrapper({ children }: { children: ReactNode }) {
  // No retry override here: the hook's own setting is what is under test, so forcing it from the
  // client would measure the fixture instead.
  const client = new QueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useCurrentUser", () => {
  it("fetches the signed-in user, and does not retry a signed-out response", async () => {
    apiFetch.mockReset();
    apiFetch.mockRejectedValue(new ApiError("signed out", 401));

    const { result } = renderHook(() => useCurrentUser(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/users/me");
    // Exactly once: a retry would sit on a signed-out session re-asking a question already answered.
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});
