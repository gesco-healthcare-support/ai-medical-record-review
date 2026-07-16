import type { ReactNode } from "react";
import { Brand } from "@/components/app/brand";

/** Sign-in chrome: navy brand-only header, calm gray-50 field, one centered card (~420px). */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-muted">
      <header className="h-12 w-full shrink-0 bg-navy-600">
        <div className="mx-auto flex h-full max-w-[1840px] items-center px-[var(--gutter)]">
          <Brand appLabel="Medical Record Review" />
        </div>
      </header>
      <main className="flex flex-1 items-center justify-center px-5 py-10">
        <div className="w-full max-w-[420px] rounded-xl border border-gray-200 bg-card p-7 shadow-md">
          {children}
        </div>
      </main>
    </div>
  );
}
