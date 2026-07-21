import type { ReactNode } from "react";
import Image from "next/image";
import { Brand } from "@/components/app/brand";

/**
 * Sign-in chrome (mirrors security/base.html): navy brand-only top bar, then a centered card
 * with the crest, gold eyebrow, and the view's heading, followed by the view's content.
 */
export function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <>
      <header className="ev-topbar">
        <Brand />
      </header>
      <main className="auth-main">
        <div className="auth-card">
          <div className="auth-head">
            <Image
              className="auth-crest"
              src="/evaluators-crest.png"
              alt=""
              width={48}
              height={50}
            />
            <div className="ev-eyebrow">Medical Record Review</div>
            <h1>{title}</h1>
            {subtitle ? <p className="auth-sub">{subtitle}</p> : null}
          </div>
          {children}
        </div>
      </main>
    </>
  );
}
