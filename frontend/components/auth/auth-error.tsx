import { AlertCircle } from "lucide-react";

/** In-card error banner (DS .auth-alert): danger band, announced to assistive tech. */
export function AuthError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="auth-alert" role="alert">
      <AlertCircle width={16} height={16} aria-hidden />
      <span>{message}</span>
    </div>
  );
}
