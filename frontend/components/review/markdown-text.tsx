import { Fragment } from "react";

// Inline emphasis the summarizer emits: **bold**, *italic*, _italic_.
//
// THREE renderers read this and only two can share code: `reporting.INLINE_EMPHASIS_RE` is the
// definition, `linked_pdf` imports it, and this is the TypeScript copy that tracks it. Keep them
// character-for-character identical - the same file pair has already diverged twice (#158, #268).
//
// Deliberately NO `s` flag, matching the Python side. With one, `\*(.+?)\*` pairs a BULLET on one
// line with the bullet on the next and italicises everything between; the Python copies carried
// `re.DOTALL` and did exactly that to 83-387 characters of three delivered documents. This
// renderer was the one that had it right, which is why the fix moved the other two.
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
export function MarkdownText({ text }: Readonly<{ text: string }>) {
  return (
    <>
      {tokenize(text).map((s, i) => {
        if (s.bold) return <strong key={i}>{s.text}</strong>;
        if (s.italic) return <em key={i}>{s.text}</em>;
        return <Fragment key={i}>{s.text}</Fragment>;
      })}
    </>
  );
}
