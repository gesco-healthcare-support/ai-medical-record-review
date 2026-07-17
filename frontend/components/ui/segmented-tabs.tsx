"use client";

import { cn } from "@/lib/utils";

/**
 * Pill-style tab switch (DS SegmentedTabs): a gray-100 track with buttons; the active tab is a
 * white pill with a soft shadow + navy text. Controlled - the parent owns which view is shown, so
 * this works both for in-page tab content (review editor) and for tabs that navigate (bundles).
 */
export function SegmentedTabs<T extends string>({
  tabs,
  value,
  onValueChange,
  ariaLabel,
}: {
  tabs: ReadonlyArray<{ value: T; label: React.ReactNode }>;
  value: T;
  onValueChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="ev-segtabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={value === tab.value}
          className={cn("ev-segtab", value === tab.value && "active")}
          onClick={() => onValueChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
