import { AppBar } from "@/components/app/app-bar";
import { BackLink } from "@/components/app/back-link";
import { AdminView } from "@/components/admin/admin-view";

/** Admin console (categories + prompts + reprocess). is_admin gated (API 403s; AdminView also
 *  shows a friendly notice). Unauthenticated requests 401 -> /login. */
export default function AdminPage() {
  return (
    <>
      <AppBar />
      <div className="ev-page-back">
        <BackLink />
      </div>
      <AdminView />
    </>
  );
}
