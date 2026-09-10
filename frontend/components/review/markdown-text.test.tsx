import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownText } from "@/components/review/markdown-text";

/** The review screen's inline-emphasis renderer, which had no tests despite being one of THREE
 *  copies of one rule - the other two are `reporting.INLINE_EMPHASIS_RE` (which `linked_pdf`
 *  imports) and this. These pin the invariant the Python side is now tested for too, so the copy
 *  that cannot share code is not the one free to drift. */
describe("MarkdownText", () => {
  function emphasised(text: string) {
    const { container } = render(<MarkdownText text={text} />);
    return [...container.querySelectorAll("strong, em")].map((n) => n.textContent);
  }

  it("renders the three markers the summarizer emits", () => {
    expect(emphasised("**bold** and *it* and _it_")).toEqual(["bold", "it", "it"]);
  });

  it("does not pair two bullets across a line", () => {
    // The Python renderers carried re.DOTALL and did exactly this, italicising 83-387 characters of
    // three delivered documents. This side was already right; the test is here so it stays right.
    expect(emphasised("* first item\n* second item\n* third item")).toEqual([]);
  });

  it("does not let an unclosed marker swallow the paragraphs after it", () => {
    expect(emphasised("**Diagnoses:\nLumbar strain**")).toEqual([]);
  });

  it("leaves text with no markers exactly as it was", () => {
    const { container } = render(<MarkdownText text="Plain clinical prose, 5 mg daily." />);
    expect(container.textContent).toBe("Plain clinical prose, 5 mg daily.");
    expect(container.querySelectorAll("strong, em")).toHaveLength(0);
  });
});
