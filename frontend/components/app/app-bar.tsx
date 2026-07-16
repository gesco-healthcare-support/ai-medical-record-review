import type { ReactNode } from "react";
import { Brand } from "./brand";
import { UserMenu } from "./user-menu";

/**
 * Navy app bar for signed-in screens (mirrors the Flask .ev-topbar). Brand on the left; an
 * optional contextual action plus the user menu, right-aligned via .ev-topbar-nav.
 */
export function AppBar({ action }: { action?: ReactNode }) {
  return (
    <header className="ev-topbar">
      <Brand />
      <div className="ev-topbar-nav">
        {action}
        <UserMenu />
      </div>
    </header>
  );
}
