"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useLogin, useRegister } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api";
import { AuthShell } from "./auth-shell";
import { AuthError } from "./auth-error";
import { PasswordChecklist, passwordValid } from "./password-checklist";

/** Registration does not start a session, so a successful create is followed by a login. */
export function RegisterForm({ onSignIn }: { onSignIn: () => void }) {
  const router = useRouter();
  const register = useRegister();
  const login = useLogin();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirmMismatch = confirm.length > 0 && confirm !== password;
  const canSubmit =
    Boolean(name.trim()) && Boolean(email) && passwordValid(password) && confirm === password;
  const busy = register.isPending || login.isPending;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setError(null);
    if (!canSubmit) return;
    try {
      await register.mutateAsync({ name: name.trim(), email, password });
      await login.mutateAsync({ email, password });
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError("An account with this email already exists.");
      } else {
        setError("Could not create your account. Check your details and try again.");
      }
    }
  }

  return (
    <AuthShell title="Create an account" subtitle="Set up access to medical record review.">
      <AuthError message={error} />
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <div className="auth-field">
          <label className="ev-lbl" htmlFor="name">
            Full name
          </label>
          <input
            id="name"
            className="ev-inp"
            placeholder="Jane Evaluator"
            autoComplete="name"
            autoFocus
            required
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="auth-field">
          <label className="ev-lbl" htmlFor="email">
            Email address
          </label>
          <input
            id="email"
            type="email"
            className="ev-inp"
            placeholder="you@email.com"
            autoComplete="email"
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
            placeholder="Create a password"
            autoComplete="new-password"
            required
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
          <PasswordChecklist password={password} />
        </div>
        <div className="auth-field">
          <label className="ev-lbl" htmlFor="confirm">
            Confirm password
          </label>
          <input
            id="confirm"
            type="password"
            className={cn("ev-inp", confirmMismatch && "invalid")}
            placeholder="Re-enter your password"
            autoComplete="new-password"
            required
            value={confirm}
            disabled={busy}
            aria-invalid={confirmMismatch}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {confirmMismatch ? <div className="auth-field-error">Passwords do not match.</div> : null}
        </div>
        <button
          type="submit"
          className="ev-btn ev-btn-primary ev-btn-block"
          disabled={busy || (submitted && !canSubmit)}
        >
          {busy ? "Creating account..." : "Create account"}
        </button>
      </form>
      <div className="auth-alt">
        Already have an account?{" "}
        <button type="button" className="auth-linkbtn" onClick={onSignIn}>
          Sign in
        </button>
      </div>
    </AuthShell>
  );
}
