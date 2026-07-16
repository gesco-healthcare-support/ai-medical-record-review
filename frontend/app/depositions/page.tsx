import { AppBar } from "@/components/app/app-bar";
import { BundlePageClient } from "@/components/bundle/bundle-page-client";

/** Depositions bundle. Unauthenticated requests 401 -> /login. */
export default function DepositionsPage() {
  return (
    <>
      <AppBar />
      <BundlePageClient
        config={{ label: "Depositions", slug: "depositions", categories: ["9"] }}
      />
    </>
  );
}
