import { Suspense } from "react";
import { LoginView } from "@/components/auth/login-view";

// LoginView reads useSearchParams, which Next requires inside a Suspense boundary.
export default function LoginPage() {
  return (
    <Suspense>
      <LoginView />
    </Suspense>
  );
}
