import { cn } from "@/lib/utils";

/**
 * Crest + EVALUATORS wordmark lockup. EVALUATORS is the only all-caps element in the UI.
 * The crest tile is a placeholder monogram for now; swap in the real crest by dropping
 * /evaluators-crest.png into public/ and rendering it here.
 */
export function Brand({ className, appLabel }: { className?: string; appLabel?: string }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <span
        aria-hidden
        className="grid size-6 place-items-center rounded-[5px] bg-gold-500 font-heading text-[13px] font-bold text-navy-900"
      >
        E
      </span>
      <span className="font-heading text-[15px] leading-none font-semibold tracking-[0.14em] text-white">
        EVALUATORS
      </span>
      {appLabel ? (
        <>
          <span aria-hidden className="hidden h-4 w-px bg-white/20 sm:block" />
          <span className="hidden text-[13px] text-on-navy sm:block">{appLabel}</span>
        </>
      ) : null}
    </span>
  );
}
