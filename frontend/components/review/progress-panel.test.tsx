import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProgressPanel } from "@/components/review/progress-panel";

describe("ProgressPanel", () => {
  it("shows what is running and how far along it is", () => {
    const { container } = render(
      <ProgressPanel title="Summarizing" pct={45} detail="18 of 40 documents" />,
    );

    expect(screen.getByRole("heading", { name: "Summarizing" })).toBeInTheDocument();
    expect(screen.getByText("18 of 40 documents")).toBeInTheDocument();
    expect((container.querySelector(".bar-fill") as HTMLElement).style.width).toBe("45%");
  });

  it("never renders an empty bar, even at zero percent", () => {
    // A job that has just been queued reports 0. Rendering that literally gives a bar with no fill
    // at all, which reads as "nothing is happening" at exactly the moment the reviewer is checking
    // whether their click worked - so the fill has a floor.
    const { container } = render(
      <ProgressPanel title="Identifying documents" pct={0} detail="starting" />,
    );

    expect((container.querySelector(".bar-fill") as HTMLElement).style.width).toBe("4%");
  });
});
