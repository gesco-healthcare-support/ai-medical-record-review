import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";

/** Reusable back link for sub-pages. Defaults to My documents (home); pass href/label to point
 *  it elsewhere on a specific page while keeping one consistent style. */
export function BackLink({
  href = "/",
  label = "My documents",
  className,
}: {
  href?: string;
  label?: string;
  className?: string;
}) {
  return (
    <Link href={href} className={cn("ev-backlink", className)}>
      <ArrowLeft width={15} height={15} aria-hidden /> {label}
    </Link>
  );
}
