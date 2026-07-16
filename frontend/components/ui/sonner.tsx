"use client";

import { Toaster as Sonner, type ToasterProps } from "sonner";
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react";

/**
 * App toasts: navy-900 surface, white text, ~3.5s, bottom-center (position set in layout).
 * This is a light-only workbench, so we pin the theme and drop the next-themes dependency
 * the shadcn default pulls in.
 */
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light"
      duration={3500}
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--color-navy-900)",
          "--normal-text": "#ffffff",
          "--normal-border": "var(--color-navy-900)",
          "--border-radius": "var(--radius-md)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
};

export { Toaster };
