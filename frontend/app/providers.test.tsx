/**
 * The shared query/mutation cache's 401 handling.
 *
 * This is the only place a signed-out session is turned into a redirect for EVERY query and
 * mutation at once, so the loop guard matters as much as the redirect: without it, a 401 served by
 * /login itself sends the browser back to /login forever. Nothing exercised either half before.
 *
 * It is also the first test under app/. The route tree has always been inside the coverage
 * `include` but was outside vitest's `test.include`, so a test placed here was silently never
 * collected - it could not fail, which is why the mutation probe on the loop guard, not this file
 * passing, is what proves the glob change took effect.
 */
import { useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "@/app/providers";
import { ApiError } from "@/lib/api";

/** jsdom's Location MEMBERS are non-configurable, so `assign` cannot be spied on in place - the
 *  whole `location` is replaced instead, which jsdom does allow. */
const assign = vi.fn();

beforeEach(() => {
  assign.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function navigateTo(pathname: string) {
  vi.stubGlobal("location", { pathname, assign });
}

/** Fails its query with `error`, so the cache-level onError the Providers install runs. */
function Failing({ error }: Readonly<{ error: unknown }>) {
  const { isError } = useQuery({
    queryKey: ["providers-spec", String(error)],
    queryFn: () => Promise.reject(error),
  });
  return <span>{isError ? "failed" : "pending"}</span>;
}

async function renderFailing(error: unknown, at: string) {
  navigateTo(at);
  render(
    <Providers>
      <Failing error={error} />
    </Providers>,
  );
  // Generous, because the configured `retry` deliberately retries a non-auth failure once and
  // TanStack backs off ~1s before it - the default 1s waitFor lands mid-backoff. An auth failure
  // is not retried and settles immediately.
  await waitFor(() => expect(screen.getByText("failed")).toBeInTheDocument(), { timeout: 5000 });
}

describe("Providers 401 handling", () => {
  it("sends the reviewer to /login when any query reports the session is gone", async () => {
    await renderFailing(new ApiError("signed out", 401), "/records/abc");
    expect(assign).toHaveBeenCalledWith("/login");
  });

  it("does NOT redirect when the 401 arrives on /login itself", async () => {
    // The loop guard. /login answering 401 is the ordinary case for a signed-out visitor, and
    // redirecting there again is an infinite reload rather than a wrong page - it would not look
    // like a routing bug to whoever hit it.
    await renderFailing(new ApiError("signed out", 401), "/login");
    expect(assign).not.toHaveBeenCalled();
  });

  it("leaves a non-401 failure where it is", async () => {
    // A 500 is the server's problem, not the session's. Redirecting on it would sign the reviewer
    // out of a working session and lose whatever they had open.
    await renderFailing(new ApiError("server exploded", 500), "/records/abc");
    expect(assign).not.toHaveBeenCalled();
  });
});
