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

    // The call count is asserted INSIDE waitFor, first, and with a window WIDER THAN react-query's
    // first retry backoff. All three parts are needed for a failure to say what broke:
    //   - outside waitFor, it is never reached and the test dies as a timeout;
    //   - second, the isError assertion fails first and reports "expected false to be true";
    //   - inside the default 1000ms window, the retry has not fired yet, so the count is still 1
    //     and the test STILL dies on isError - measured, not assumed.
    // At 3000ms the retry has landed, so a hook that retries fails as
    // "expected spy to be called 1 times, but got 2 times", which names the guarantee.
    // Unbroken, the query settles immediately and none of this costs anything.
    await waitFor(
      () => {
        expect(apiFetch).toHaveBeenCalledTimes(1);
        expect(result.current.isError).toBe(true);
      },
      { timeout: 3000 },
    );
    expect(apiFetch).toHaveBeenCalledWith("/users/me");
  });
});
