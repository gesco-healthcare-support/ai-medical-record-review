"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FileStack, LogOut, ShieldCheck, Stethoscope } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { apiFetch } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

function initialsFrom(name: string | null | undefined, email: string | undefined): string {
  const source = name?.trim() || email || "";
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((p) => p[0]);
  return (letters.join("") || "?").toUpperCase();
}

/** Avatar + name trigger with navigation shortcuts and sign out. Admin item is gated. */
export function UserMenu() {
  const { data: user } = useCurrentUser();
  const router = useRouter();
  const queryClient = useQueryClient();

  async function signOut() {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // Session may already be gone; clear local state and continue to login anyway.
    }
    queryClient.clear();
    router.push("/login");
  }

  const displayName = user?.name || user?.email || "Account";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-2 rounded-md px-1.5 py-1 text-sm text-white outline-none hover:bg-white/10 focus-visible:bg-white/10">
        <span className="grid size-7 place-items-center rounded-full bg-white/15 text-[12px] font-semibold">
          {initialsFrom(user?.name, user?.email)}
        </span>
        <span className="hidden max-w-[160px] truncate sm:block">{displayName}</span>
        <ChevronDown className="size-4 text-on-navy" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="flex flex-col">
          <span className="truncate font-medium">{user?.name ?? "Signed in"}</span>
          {user?.email ? (
            <span className="truncate text-xs font-normal text-muted-foreground">{user.email}</span>
          ) : null}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/diagnostics">
            <Stethoscope aria-hidden /> Diagnostic &amp; Operative
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/depositions">
            <FileStack aria-hidden /> Depositions
          </Link>
        </DropdownMenuItem>
        {user?.is_superuser ? (
          <DropdownMenuItem asChild>
            <Link href="/admin">
              <ShieldCheck aria-hidden /> Admin
            </Link>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={signOut}>
          <LogOut aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
