import { AppBar } from "@/components/app/app-bar";
import { BackLink } from "@/components/app/back-link";
import { BundlePageClient } from "@/components/bundle/bundle-page-client";
import { DEPOSITIONS } from "@/lib/bundle-api";

/** Depositions bundle. Unauthenticated requests 401 -> /login. */
export default function DepositionsPage() {
  return (
    <>
      <AppBar />
      <div className="ev-page-back">
        <BackLink />
      </div>
      <BundlePageClient config={DEPOSITIONS} />
    </>
  );
}
