import Image from "next/image";

/** Left cluster of the navy top bar: crest chip + EVALUATORS wordmark + divider + app label.
 *  Rendered as a fragment inside a `.ev-topbar` header (which supplies the flex row + gap). */
export function Brand() {
  return (
    <>
      <span className="ev-crest-chip">
        <Image src="/evaluators-crest.png" alt="Evaluators crest" width={28} height={29} priority />
      </span>
      <span className="ev-wordmark">EVALUATORS</span>
      <span className="ev-topbar-divider" aria-hidden />
      <span className="ev-topbar-app">Medical Record Review</span>
    </>
  );
}
