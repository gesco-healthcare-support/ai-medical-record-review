import { AppBar } from "@/components/app/app-bar";
import { BundlePageClient } from "@/components/bundle/bundle-page-client";

/** Diagnostic & Operative bundle. Unauthenticated requests 401 -> /login. */
export default function DiagnosticsPage() {
  return (
    <>
      <AppBar />
      <BundlePageClient
        config={{
          label: "Diagnostic & Operative",
          slug: "diagnostic-operative",
          categories: ["3", "8"],
        }}
      />
    </>
  );
}
