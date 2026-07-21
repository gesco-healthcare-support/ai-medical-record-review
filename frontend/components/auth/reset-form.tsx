"use client";

import { useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useResetPassword } from "@/hooks/use-auth";
import { AuthShell } from "./auth-shell";
import { AuthError } from "./auth-error";
import { PasswordChecklist, passwordValid } from "./password-checklist";

/** Consumes the reset token from the link (?token=...); email delivery is deferred, so in dev
 *  the token comes from the server log. */
export function ResetForm({ token, onSignIn }: { token: string; onSignIn: () => void }) {
  const reset = useResetPassword();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const confirmMismatch = confirm.length > 0 && confirm !== password;
  const canSubmit = passwordValid(password) && confirm === password;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!canSubmit) return;
    try {
      await reset.mutateAsync({ token, password });
      setDone(true);
    } catch {
      setError("This reset link is invalid or has expired. Request a new one.");
    }
  }

  if (!token) {
    return (
      <AuthShell
        title="Link expired"
        subtitle="This password reset link is invalid or has expired."
      >
        <button type="button" className="ev-btn ev-btn-outline ev-btn-block" onClick={onSignIn}>
          Back to sign in
        </button>
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell title="Password updated" subtitle="Sign in with your new password.">
        <div className="text-center">
          <CheckCircle2 width={40} height={40} color="var(--success-500)" aria-hidden />
        </div>
        <button type="button" className="ev-btn ev-btn-primary ev-btn-block" onClick={onSignIn}>
          Continue to sign in
        </button>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Set a new password" subtitle="Choose a password for your account.">
      <AuthError message={error} />
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <div className="auth-field">
          <label className="ev-lbl" htmlFor="password">
            New password
          </label>
          <input
            id="password"
            type="password"
            className="ev-inp"
            placeholder="Create a password"
            autoComplete="new-password"
            autoFocus
            required
            value={password}
            disabled={reset.isPending}
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
            disabled={reset.isPending}
            aria-invalid={confirmMismatch}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {confirmMismatch ? <div className="auth-field-error">Passwords do not match.</div> : null}
        </div>
        <button
          type="submit"
          className="ev-btn ev-btn-primary ev-btn-block"
          disabled={reset.isPending}
        >
          {reset.isPending ? "Updating..." : "Update password"}
        </button>
      </form>
    </AuthShell>
  );
}
