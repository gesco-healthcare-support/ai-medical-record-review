import { AppBar } from "@/components/app/app-bar";
import { DocumentsView } from "@/components/documents/documents-view";

/** My documents - the signed-in landing screen. Unauthenticated requests 401 -> /login. */
export default function HomePage() {
  return (
    <>
      <AppBar />
      <DocumentsView />
    </>
  );
}
