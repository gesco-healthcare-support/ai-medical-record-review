"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useLogin } from "@/hooks/use-auth";
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
      setError("Email or password is incorrect.");
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <h1 className="font-heading text-xl font-semibold text-navy-600">Sign in</h1>
      <p className="mt-1 mb-5 text-sm text-muted-foreground">Access your medical record reviews.</p>
      <AuthError message={error} />
      <div className="grid gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            autoFocus
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
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <Checkbox
            checked={remember}
            onCheckedChange={(v) => setRemember(v === true)}
            disabled={busy}
          />
          Remember me
        </label>
        <Button type="submit" className="h-10 w-full" disabled={busy}>
          {busy ? (
            <>
              <Loader2 className="animate-spin" aria-hidden /> Signing in...
            </>
          ) : (
            "Sign in"
          )}
        </Button>
      </div>
      <div className="mt-5 flex items-center justify-between text-sm">
        <button type="button" onClick={onRegister} className="text-secondary hover:underline">
          Create account
        </button>
        <button type="button" onClick={onForgot} className="text-secondary hover:underline">
          Forgot password?
        </button>
      </div>
    </form>
  );
}
