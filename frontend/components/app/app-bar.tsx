import Link from "next/link";
import type { ReactNode } from "react";
import { Brand } from "./brand";
import { UserMenu } from "./user-menu";

/**
 * The only fixed element: navy bar with the brand lockup on the left and, on the right,
 * an optional contextual action (e.g. Upload on My documents) plus the user menu.
 */
export function AppBar({ action }: { action?: ReactNode }) {
  return (
    <header className="sticky top-0 z-50 h-12 w-full bg-navy-600 text-white">
      <div className="mx-auto flex h-full w-full max-w-[1840px] items-center justify-between px-[var(--gutter)]">
        <Link href="/" className="flex items-center" aria-label="MRR AI home">
          <Brand appLabel="Medical Record Review" />
        </Link>
        <div className="flex items-center gap-2">
          {action}
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
