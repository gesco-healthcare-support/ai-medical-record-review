import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Stepper } from "@/components/review/stepper";

describe("Stepper", () => {
  it("marks the active step and reports which step was clicked", () => {
    const onStep = vi.fn();
    render(<Stepper activeStep="review" busy={false} onStep={onStep} />);

    expect(screen.getAllByRole("button")).toHaveLength(3);
    // aria-current is what tells a screen reader where the reviewer is in the pipeline; the class
    // that styles it says nothing to anyone not looking at the screen.
    expect(screen.getByRole("button", { name: /Review & correct/ })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("button", { name: /Identify documents/ })).not.toHaveAttribute(
      "aria-current",
    );

    fireEvent.click(screen.getByRole("button", { name: /Summaries/ }));
    expect(onStep).toHaveBeenCalledWith("summaries");
  });

  it("disables every step while a job is running", () => {
    // Moving step mid-job is how a reviewer ends up looking at a half-written document set, so the
    // guard is on all three rather than only the ones ahead.
    render(<Stepper activeStep="identify" busy onStep={vi.fn()} />);
    for (const step of screen.getAllByRole("button")) {
      expect(step).toBeDisabled();
    }
  });
});
