"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useLogin } from "@/hooks/use-auth";
import { AuthShell } from "./auth-shell";
import { AuthError } from "./auth-error";

/** "Remember me" is presentational: the backend session lifetime is fixed server-side. */
export function SignInForm({
  onRegister,
  onForgot,
}: {
  onRegister: () => void;
  onForgot: () => void;
}) {
  const router = useRouter();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = login.isPending;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ email, password });
      router.replace("/");
    } catch {
      setError("We couldn't sign you in. Check your email and password, then try again.");
    }
  }

  return (
    <AuthShell title="Sign in" subtitle="Welcome back. Sign in to your workspace.">
      <AuthError message={error} />
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <div className="auth-field">
          <label className="ev-lbl" htmlFor="email">
            Email address
          </label>
          <input
            id="email"
            type="email"
            className="ev-inp"
            placeholder="you@practice.com"
            autoComplete="email"
            autoFocus
            required
            value={email}
            disabled={busy}
            onChange={(e) => {
              setEmail(e.target.value);
              if (error) setError(null);
            }}
          />
        </div>
        <div className="auth-field">
          <label className="ev-lbl" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            type="password"
            className="ev-inp"
            placeholder="Your password"
            autoComplete="current-password"
            required
            value={password}
            disabled={busy}
            onChange={(e) => {
              setPassword(e.target.value);
              if (error) setError(null);
            }}
          />
        </div>
        <div className="flex items-center justify-between">
          <label className="auth-remember">
            <input
              type="checkbox"
              className="ev-cb"
              checked={remember}
              disabled={busy}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember me
          </label>
          <button type="button" className="auth-linkbtn" onClick={onForgot}>
            Forgot password?
          </button>
        </div>
        <button type="submit" className="ev-btn ev-btn-primary ev-btn-block" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <div className="auth-alt">
        No account yet?{" "}
        <button type="button" className="auth-linkbtn" onClick={onRegister}>
          Create an account
        </button>
      </div>
    </AuthShell>
  );
}
