import { Check, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

/** Password rules mirror the backend validate_password (8+, one digit, one symbol). */
export const passwordRules = [
  { label: "8+ characters", test: (p: string) => p.length >= 8 },
  { label: "one number", test: (p: string) => /\d/.test(p) },
  { label: "one symbol", test: (p: string) => /[^A-Za-z0-9]/.test(p) },
] as const;

export function passwordValid(password: string): boolean {
  return passwordRules.every((rule) => rule.test(password));
}

/** Live requirements list (DS .auth-checklist): each item flips gray to green as it is met. */
export function PasswordChecklist({ password }: { password: string }) {
  return (
    <div className="auth-checklist" aria-live="polite">
      {passwordRules.map((rule) => {
        const met = rule.test(password);
        return (
          <div key={rule.label} className={cn("auth-check", met ? "met" : "unmet")}>
            <span className="auth-check-icon">
              {met ? (
                <Check width={14} height={14} aria-hidden />
              ) : (
                <Circle width={14} height={14} aria-hidden />
              )}
            </span>
            {rule.label}
          </div>
        );
      })}
    </div>
  );
}
