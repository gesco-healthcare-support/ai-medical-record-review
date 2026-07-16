import Image from "next/image";
import Link from "next/link";

/** Left cluster of the navy top bar: crest chip + EVALUATORS wordmark + divider + app label.
 *  Rendered as a fragment inside a `.ev-topbar` header (which supplies the flex row + gap).
 *  On signed-in screens the crest links home (My documents). */
export function Brand({ homeLink = false }: { homeLink?: boolean }) {
  const crest = (
    <Image src="/evaluators-crest.png" alt="Evaluators crest" width={28} height={29} priority />
  );
  return (
    <>
      {homeLink ? (
        <Link href="/" className="ev-crest-chip" aria-label="My documents">
          {crest}
        </Link>
      ) : (
        <span className="ev-crest-chip">{crest}</span>
      )}
      <span className="ev-wordmark">EVALUATORS</span>
      <span className="ev-topbar-divider" aria-hidden />
      <span className="ev-topbar-app">Medical Record Review</span>
    </>
  );
}
