"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Resizable two-pane split (DS SplitPane): left pane sized by a drag handle, right pane fills the
 * rest. The left width (a percentage) persists to localStorage under storageKey. Below 900px the
 * panes stack vertically and the handle is hidden (see .ev-split in evaluators-ds.css). The handle
 * is keyboard-operable (Left/Right arrows nudge 2%) for accessibility.
 */
export function SplitPane({
  left,
  right,
  storageKey,
  defaultLeft = 58,
  min = 24,
  max = 70,
}: {
  left: ReactNode;
  right: ReactNode;
  storageKey?: string;
  defaultLeft?: number;
  min?: number;
  max?: number;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [pct, setPct] = useState(defaultLeft);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!storageKey) return;
    const saved = Number(localStorage.getItem(storageKey));
    if (Number.isFinite(saved) && saved >= min && saved <= max) setPct(saved);
  }, [storageKey, min, max]);

  const persist = useCallback(
    (next: number) => {
      if (storageKey) localStorage.setItem(storageKey, String(Math.round(next)));
    },
    [storageKey],
  );

  const setClamped = useCallback(
    (clientX: number) => {
      const el = rootRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const next = Math.min(max, Math.max(min, ((clientX - rect.left) / rect.width) * 100));
      setPct(next);
    },
    [min, max],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => setClamped(e.clientX);
    const onUp = () => {
      setDragging(false);
      setPct((p) => {
        persist(p);
        return p;
      });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [dragging, setClamped, persist]);

  function nudge(delta: number) {
    setPct((p) => {
      const next = Math.min(max, Math.max(min, p + delta));
      persist(next);
      return next;
    });
  }

  return (
    <div
      ref={rootRef}
      className={cn("ev-split", dragging && "dragging")}
      style={{ "--split-left": `${pct}%` } as CSSProperties}
    >
      <div className="ev-split-pane ev-split-left">{left}</div>
      <div
        className="ev-split-handle"
        role="separator"
        tabIndex={0}
        aria-orientation="vertical"
        aria-label="Resize panels"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={min}
        aria-valuemax={max}
        onPointerDown={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") nudge(-2);
          else if (e.key === "ArrowRight") nudge(2);
        }}
      />
      <div className="ev-split-pane ev-split-right">{right}</div>
    </div>
  );
}
