"use client";

import { useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useResetPassword } from "@/hooks/use-auth";
import { PasswordChecklist, passwordValid } from "./password-checklist";
import { AuthError } from "./auth-error";

/** Consumes the token from the reset link (?token=...); email delivery is deferred, so in dev
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
      <div className="text-center">
        <h1 className="font-heading text-xl font-semibold text-navy-600">Link expired</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This password reset link is invalid or has expired. Request a new one from the sign-in page.
        </p>
        <Button variant="outline" className="mt-6 h-10 w-full" onClick={onSignIn}>
          Back to sign in
        </Button>
      </div>
    );
  }

  if (done) {
    return (
      <div className="text-center">
        <div className="mx-auto mb-3 grid size-12 place-items-center rounded-full bg-success-soft text-success">
          <CheckCircle2 aria-hidden />
        </div>
        <h1 className="font-heading text-xl font-semibold text-navy-600">Password updated</h1>
        <p className="mt-2 text-sm text-muted-foreground">Sign in with your new password.</p>
        <Button className="mt-6 h-10 w-full" onClick={onSignIn}>
          Continue to sign in
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <h1 className="font-heading text-xl font-semibold text-navy-600">Set a new password</h1>
      <p className="mt-1 mb-5 text-sm text-muted-foreground">Choose a password for your account.</p>
      <AuthError message={error} />
      <div className="grid gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="password">New password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            autoFocus
            value={password}
            disabled={reset.isPending}
            onChange={(e) => setPassword(e.target.value)}
          />
          <PasswordChecklist password={password} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="confirm">Confirm password</Label>
          <Input
            id="confirm"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            disabled={reset.isPending}
            aria-invalid={confirmMismatch}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {confirmMismatch ? <p className="text-xs text-danger">Passwords do not match.</p> : null}
        </div>
        <Button type="submit" className="h-10 w-full" disabled={reset.isPending}>
          {reset.isPending ? (
            <>
              <Loader2 className="animate-spin" aria-hidden /> Updating...
            </>
          ) : (
            "Update password"
          )}
        </Button>
      </div>
    </form>
  );
}
