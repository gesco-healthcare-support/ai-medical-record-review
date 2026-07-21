"use client";

import { useState } from "react";
import { MailCheck } from "lucide-react";
import { useForgotPassword } from "@/hooks/use-auth";
import { AuthShell } from "./auth-shell";

/** Always shows the confirmation view on submit (no account enumeration; the backend 202s). */
export function ForgotForm({ onSignIn }: { onSignIn: () => void }) {
  const forgot = useForgotPassword();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await forgot.mutateAsync(email);
    } catch {
      // No enumeration: show success regardless of outcome.
    }
    setSent(true);
  }

  if (sent) {
    return (
      <AuthShell title="Check your email">
        <div className="text-center">
          <MailCheck width={40} height={40} color="var(--success-500)" aria-hidden />
          <p className="muted" style={{ marginTop: 12 }}>
            If an account exists for {email || "that address"}, we sent a link to reset your
            password.
          </p>
        </div>
        <button type="button" className="ev-btn ev-btn-outline ev-btn-block" onClick={onSignIn}>
          Back to sign in
        </button>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Reset your password"
      subtitle="Enter your email and we'll send a link to set a new one."
    >
      <form className="auth-form" onSubmit={onSubmit} noValidate>
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
            autoFocus
            required
            value={email}
            disabled={forgot.isPending}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <button
          type="submit"
          className="ev-btn ev-btn-primary ev-btn-block"
          disabled={forgot.isPending}
        >
          {forgot.isPending ? "Sending..." : "Send reset link"}
        </button>
      </form>
      <div className="auth-alt">
        <button type="button" className="auth-linkbtn" onClick={onSignIn}>
          Back to sign in
        </button>
      </div>
    </AuthShell>
  );
}
