import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StartPanel } from "@/components/review/start-panel";

describe("StartPanel", () => {
  it("offers the first-run wording and starts identification", () => {
    const onStart = vi.fn();
    render(<StartPanel rerun={false} onStart={onStart} />);

    expect(
      screen.getByRole("heading", { name: /Ready to identify documents/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/split into its component documents/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Identify documents/ }));
    expect(onStart).toHaveBeenCalled();
  });

  it("warns that a re-run replaces every correction already made", () => {
    // The two wordings say materially different things. On a re-run the reviewer is about to lose
    // every boundary and category they fixed by hand, and the only thing that tells them so is this
    // copy - so it is pinned rather than left to whoever edits the string next.
    render(<StartPanel rerun onStart={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: /Re-run document identification/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/including every correction you made/)).toBeInTheDocument();
  });

  it("lets the caller replace the hint, and can be disabled", () => {
    render(<StartPanel rerun={false} disabled hint="A job is already running." onStart={vi.fn()} />);

    expect(screen.getByText("A job is already running.")).toBeInTheDocument();
    expect(screen.queryByText(/split into its component documents/)).toBeNull();
    expect(screen.getByRole("button", { name: /Identify documents/ })).toBeDisabled();
  });
});
