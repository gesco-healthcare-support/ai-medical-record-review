"use client";

import { useState } from "react";
import { Loader2, MailCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useForgotPassword } from "@/hooks/use-auth";

/** Always shows the confirmation view on submit (the backend never reveals whether the
 *  email exists), matching the no-enumeration 202 response. */
export function ForgotForm({ onSignIn }: { onSignIn: () => void }) {
  const forgot = useForgotPassword();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await forgot.mutateAsync(email);
    } catch {
      // No enumeration: show success regardless of the outcome.
    }
    setSent(true);
  }

  if (sent) {
    return (
      <div className="text-center">
        <div className="mx-auto mb-3 grid size-12 place-items-center rounded-full bg-success-soft text-success">
          <MailCheck aria-hidden />
        </div>
        <h1 className="font-heading text-xl font-semibold text-navy-600">Check your email</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          If an account exists for {email || "that address"}, we sent a link to reset your password.
        </p>
        <Button variant="outline" className="mt-6 h-10 w-full" onClick={onSignIn}>
          Back to sign in
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <h1 className="font-heading text-xl font-semibold text-navy-600">Reset your password</h1>
      <p className="mt-1 mb-5 text-sm text-muted-foreground">
        Enter your email and we&apos;ll send a link to set a new one.
      </p>
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
            disabled={forgot.isPending}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <Button type="submit" className="h-10 w-full" disabled={forgot.isPending}>
          {forgot.isPending ? (
            <>
              <Loader2 className="animate-spin" aria-hidden /> Sending...
            </>
          ) : (
            "Send reset link"
          )}
        </Button>
      </div>
      <div className="mt-5 text-center text-sm">
        <button type="button" onClick={onSignIn} className="text-secondary hover:underline">
          Back to sign in
        </button>
      </div>
    </form>
  );
}
