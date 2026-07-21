import { AppBar } from "@/components/app/app-bar";
import { ReviewPageClient } from "@/components/review/review-page-client";

/** Review editor for one record. Unauthenticated requests 401 -> /login. */
export default async function RecordPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <AppBar />
      <ReviewPageClient documentId={id} />
    </>
  );
}
