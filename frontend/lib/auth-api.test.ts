/**
 * The auth client calls.
 *
 * Stubbed at `fetch` rather than at `apiFetch`, so the assertions cover the request the browser
 * would really send - including the `/api` prefix and the content type. That matters more here than
 * anywhere else in the codebase, because `login` is the ONE call that is not JSON, and stubbing
 * `apiFetch` would hide exactly the behaviour that makes it work.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forgotPassword, login, logout, register, resetPassword } from "@/lib/auth-api";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall() {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return { url: url as string, init: init as RequestInit };
}

const headerOf = (init: RequestInit, name: string) => new Headers(init.headers).get(name);

describe("login", () => {
  it("posts the credentials form-encoded, under `username` rather than `email`", async () => {
    await login("reviewer@example.test", "correct horse battery staple");

    const { url, init } = lastCall();
    expect(url).toBe("/api/auth/login");
    expect(init.method).toBe("POST");

    // The server side is FastAPI-Users' OAuth2PasswordRequestForm, which reads a FORM body whose
    // field is `username`. Sending JSON here - the obvious tidy-up, since every other call in the
    // codebase is JSON - is rejected by the server and reaches the user as "bad credentials".
    expect(headerOf(init, "Content-Type")).toBe("application/x-www-form-urlencoded");
    expect(String(init.body)).toBe(
      "username=reviewer%40example.test&password=correct+horse+battery+staple",
    );
  });

  it("carries the credentials the session cookie needs", async () => {
    await login("reviewer@example.test", "x");
    expect(lastCall().init.credentials).toBe("include");
  });
});

describe("the JSON auth calls", () => {
  it("each post to their own endpoint with the caller's values", async () => {
    // Four near-identical wrappers. One pointed at the wrong path still resolves, and a reset that
    // quietly ran forgot-password would tell the user to check their email for ever.
    await logout();
    expect(lastCall().url).toBe("/api/auth/logout");
    expect(lastCall().init.method).toBe("POST");

    await register({
      name: "Sam Reviewer",
      email: "reviewer@example.test",
      password: "correct horse battery staple",
    });
    expect(lastCall().url).toBe("/api/auth/register");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({
      name: "Sam Reviewer",
      email: "reviewer@example.test",
      password: "correct horse battery staple",
    });

    await forgotPassword("reviewer@example.test");
    expect(lastCall().url).toBe("/api/auth/forgot-password");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ email: "reviewer@example.test" });

    await resetPassword("tok-123", "a brand new one");
    expect(lastCall().url).toBe("/api/auth/reset-password");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({
      token: "tok-123",
      password: "a brand new one",
    });
  });
});
