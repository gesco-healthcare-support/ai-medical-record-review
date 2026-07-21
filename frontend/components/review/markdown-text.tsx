import { Fragment } from "react";

// Inline emphasis the summarizer emits: **bold**, *italic*, _italic_. Mirrors the Word export's
// run parser (reporting._add_inline_runs) so the page and the .docx render the same way.
const INLINE_RE = /\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_/g;

type Seg = { text: string; bold?: boolean; italic?: boolean };

function tokenize(input: string): Seg[] {
  const segs: Seg[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  INLINE_RE.lastIndex = 0;
  while ((m = INLINE_RE.exec(input)) !== null) {
    if (m.index > last) segs.push({ text: input.slice(last, m.index) });
    if (m[1] !== undefined) segs.push({ text: m[1], bold: true });
    else segs.push({ text: (m[2] ?? m[3]) as string, italic: true });
    last = INLINE_RE.lastIndex;
  }
  if (last < input.length) segs.push({ text: input.slice(last) });
  return segs;
}

/** Render the summarizer's inline markdown (**bold**, *italic*, _italic_) as real emphasis so no
 *  raw markers show. Anything else is passed through as plain text. */
export function MarkdownText({ text }: { text: string }) {
  return (
    <>
      {tokenize(text).map((s, i) =>
        s.bold ? (
          <strong key={i}>{s.text}</strong>
        ) : s.italic ? (
          <em key={i}>{s.text}</em>
        ) : (
          <Fragment key={i}>{s.text}</Fragment>
        ),
      )}
    </>
  );
}
