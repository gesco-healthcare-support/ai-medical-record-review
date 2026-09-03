import { describe, expect, it } from "vitest";
import { displayTitle } from "@/components/review/summaries-view";

/** The engine bakes "[ManualCheck] ", " (Pages n-m)" and " [Diagnostic Study]" into the stored
 *  title; the reading column strips them because it shows the same information as chips. */
describe("displayTitle", () => {
  it("strips each decoration, alone and combined", () => {
    expect(displayTitle("Progress Note (Pages 1-2)")).toBe("Progress Note");
    expect(displayTitle("[ManualCheck] Progress Note")).toBe("Progress Note");
    expect(displayTitle("MRI Report [Diagnostic Study]")).toBe("MRI Report");
    expect(displayTitle("[ManualCheck] MRI Report (Pages 3-5)")).toBe("MRI Report");
    expect(displayTitle("Operative Report")).toBe("Operative Report");
    expect(displayTitle("")).toBe("");
  });

  it("leaves text that merely resembles a decoration", () => {
    expect(displayTitle("Pages of a notebook")).toBe("Pages of a notebook");
    expect(displayTitle("  Leading space is kept")).toBe("  Leading space is kept");
  });

  it("returns promptly on a whitespace-only title", () => {
    // The reason the two suffix patterns lost their leading `\s*`: as `\s*MARKER...$` the engine
    // re-scans the whitespace run from every start position, so cost grows with the square of the
    // length - 20,000 spaces measured 424ms, and titles come out of the model. Anything above a
    // few milliseconds here means that shape has come back.
    const hostile = " ".repeat(20_000);
    const started = performance.now();
    expect(displayTitle(hostile)).toBe(hostile);
    expect(performance.now() - started).toBeLessThan(100);
  });
});
