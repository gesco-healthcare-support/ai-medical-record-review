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

/** Live requirements list: each item flips gray to green as it is satisfied while typing. */
export function PasswordChecklist({ password }: { password: string }) {
  return (
    <ul className="mt-2 grid gap-1" aria-live="polite">
      {passwordRules.map((rule) => {
        const ok = rule.test(password);
        return (
          <li
            key={rule.label}
            className={cn(
              "flex items-center gap-1.5 text-xs transition-colors",
              ok ? "text-success" : "text-muted-foreground",
            )}
          >
            {ok ? <Check className="size-3.5" aria-hidden /> : <Circle className="size-3.5" aria-hidden />}
            {rule.label}
          </li>
        );
      })}
    </ul>
  );
}
