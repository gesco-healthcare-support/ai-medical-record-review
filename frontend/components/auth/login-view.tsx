"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "./auth-shell";
import { SignInForm } from "./sign-in-form";
import { RegisterForm } from "./register-form";
import { ForgotForm } from "./forgot-form";
import { ResetForm } from "./reset-form";
import { useCurrentUser } from "@/hooks/use-current-user";

type View = "signin" | "register" | "forgot" | "reset";

/** Single-card auth flow. Initial view comes from ?view / ?token; already-signed-in users
 *  are bounced to the app. */
export function LoginView() {
  const params = useSearchParams();
  const token = params.get("token");
  const viewParam = params.get("view");
  const initialView: View =
    viewParam === "register" || viewParam === "forgot" || viewParam === "reset"
      ? viewParam
      : token
        ? "reset"
        : "signin";

  const [view, setView] = useState<View>(initialView);
  const router = useRouter();
  const { data: user } = useCurrentUser();

  useEffect(() => {
    if (user) router.replace("/");
  }, [user, router]);

  return (
    <AuthShell>
      {view === "signin" && (
        <SignInForm onRegister={() => setView("register")} onForgot={() => setView("forgot")} />
      )}
      {view === "register" && <RegisterForm onSignIn={() => setView("signin")} />}
      {view === "forgot" && <ForgotForm onSignIn={() => setView("signin")} />}
      {view === "reset" && <ResetForm token={token ?? ""} onSignIn={() => setView("signin")} />}
    </AuthShell>
  );
}
