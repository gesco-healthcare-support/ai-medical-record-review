import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

const MAX_WIDTH = {
  wide: "max-w-[1840px]", // data screens: My documents, Bundles, Admin
  reading: "max-w-[1100px]", // Summaries reading column
  narrow: "max-w-[980px]", // empty / onboarding column
  full: "max-w-none", // full-bleed (Review editor split view)
} as const;

/**
 * Fluid page container. Horizontal gutters scale with the viewport (var(--gutter) =
 * clamp(20px, 4vw, 96px)) and content centers under a per-screen max width, so wide
 * monitors fill proportionally with no dead side-bands.
 */
export function Container({
  size = "wide",
  className,
  children,
}: {
  size?: keyof typeof MAX_WIDTH;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("mx-auto w-full px-[var(--gutter)]", MAX_WIDTH[size], className)}>
      {children}
    </div>
  );
}
