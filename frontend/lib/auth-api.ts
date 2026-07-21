import { apiFetch } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

/**
 * Auth calls against the FastAPI-Users routers (/api/auth). Login is form-encoded
 * (OAuth2PasswordRequestForm: username = email); the rest are JSON. All rely on the
 * SameSite=Lax session cookie set by the backend.
 */

export async function login(email: string, password: string): Promise<void> {
  const body = new URLSearchParams({ username: email, password });
  await apiFetch<null>("/auth/login", {
    method: "POST",
    body,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
}

export async function logout(): Promise<void> {
  await apiFetch<null>("/auth/logout", { method: "POST" });
}

export async function register(input: {
  name: string;
  email: string;
  password: string;
}): Promise<CurrentUser> {
  return apiFetch<CurrentUser>("/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function forgotPassword(email: string): Promise<void> {
  await apiFetch<null>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function resetPassword(token: string, password: string): Promise<void> {
  await apiFetch<null>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });
}
