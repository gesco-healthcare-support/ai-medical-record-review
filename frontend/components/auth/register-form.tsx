"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLogin, useRegister } from "@/hooks/use-auth";
import { PasswordChecklist, passwordValid } from "./password-checklist";
import { AuthError } from "./auth-error";
import { ApiError } from "@/lib/api";

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
  const canSubmit = Boolean(name.trim()) && Boolean(email) && passwordValid(password) && confirm === password;
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
    <form onSubmit={onSubmit} noValidate>
      <h1 className="font-heading text-xl font-semibold text-navy-600">Create account</h1>
      <p className="mt-1 mb-5 text-sm text-muted-foreground">Set up access to medical record review.</p>
      <AuthError message={error} />
      <div className="grid gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="name">Full name</Label>
          <Input
            id="name"
            autoComplete="name"
            required
            autoFocus
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
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
        <div className="grid gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            disabled={busy}
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
            disabled={busy}
            aria-invalid={confirmMismatch}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {confirmMismatch ? (
            <p className="text-xs text-danger">Passwords do not match.</p>
          ) : null}
        </div>
        <Button type="submit" className="h-10 w-full" disabled={busy || (submitted && !canSubmit)}>
          {busy ? (
            <>
              <Loader2 className="animate-spin" aria-hidden /> Creating account...
            </>
          ) : (
            "Create account"
          )}
        </Button>
      </div>
      <div className="mt-5 text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <button type="button" onClick={onSignIn} className="text-secondary hover:underline">
          Sign in
        </button>
      </div>
    </form>
  );
}
