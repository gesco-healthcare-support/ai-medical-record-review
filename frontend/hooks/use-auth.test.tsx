/**
 * The auth mutations.
 *
 * `useLogin`'s cache invalidation is the one with teeth: it is what makes the rest of the app
 * notice a sign-in. Without it the route guards and the user menu keep rendering the signed-out
 * state until something unrelated happens to refetch, which reads as "the login did not work".
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/auth-api", () => ({
  login: vi.fn().mockResolvedValue(undefined),
  logout: vi.fn().mockResolvedValue(undefined),
  register: vi.fn().mockResolvedValue({ id: "u1", email: "reviewer@example.test" }),
  forgotPassword: vi.fn().mockResolvedValue(undefined),
  resetPassword: vi.fn().mockResolvedValue(undefined),
}));

import { forgotPassword, login, register, resetPassword } from "@/lib/auth-api";
import {
  useForgotPassword,
  useLogin,
  useRegister,
  useResetPassword,
} from "@/hooks/use-auth";

function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated: unknown[] = [];
  vi.spyOn(client, "invalidateQueries").mockImplementation(async (filters) => {
    invalidated.push(filters?.queryKey);
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, invalidated };
}

describe("useLogin", () => {
  it("signs in and refreshes the cached current user", async () => {
    const { wrapper, invalidated } = harness();
    const { result } = renderHook(() => useLogin(), { wrapper });

    await result.current.mutateAsync({
      email: "reviewer@example.test",
      password: "correct horse battery staple",
    });

    expect(login).toHaveBeenCalledWith("reviewer@example.test", "correct horse battery staple");
    await waitFor(() => expect(invalidated).toEqual([["current-user"]]));
  });
});

describe("the remaining auth mutations", () => {
  it("each reaches its own endpoint with what the caller passed", async () => {
    // Four near-identical one-line wrappers: one pointed at the wrong call would still resolve, and
    // a reset that quietly ran forgot-password would tell the user to check their email forever.
    const { wrapper } = harness();

    const registration = renderHook(() => useRegister(), { wrapper });
    await registration.result.current.mutateAsync({
      name: "Sam Reviewer",
      email: "reviewer@example.test",
      password: "correct horse battery staple",
    });
    // First argument only: useRegister passes the api function straight through as its mutationFn,
    // so react-query also hands it a context object - unlike the other three, which wrap in an
    // arrow. What matters is that the registration details arrive intact.
    expect(vi.mocked(register).mock.calls[0][0]).toEqual({
      name: "Sam Reviewer",
      email: "reviewer@example.test",
      password: "correct horse battery staple",
    });

    const forgot = renderHook(() => useForgotPassword(), { wrapper });
    await forgot.result.current.mutateAsync("reviewer@example.test");
    expect(forgotPassword).toHaveBeenCalledWith("reviewer@example.test");

    const reset = renderHook(() => useResetPassword(), { wrapper });
    await reset.result.current.mutateAsync({ token: "tok-123", password: "a brand new one" });
    expect(resetPassword).toHaveBeenCalledWith("tok-123", "a brand new one");
  });
});
