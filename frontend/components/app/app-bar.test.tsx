/**
 * The navy bar across the top of every signed-in screen.
 *
 * `AppBar` is composition and nothing else - the brand, an optional page action, the user menu - so
 * each test pins that one of those pieces arrives. None of them opens the menu: that is
 * `user-menu.test.tsx`'s job, and opening a Radix menu is the slow path in this suite.
 *
 * THE HOME LINK IS THE PART WITH TEETH. `Brand` renders the crest as a link only when `homeLink` is
 * passed, and the sign-in screens render it without one on purpose. So `<Brand />` here would be a
 * silent regression: the logo would still show, and would no longer take a reviewer home.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({
    data: { name: "Sam Reviewer", email: "reviewer@example.test" },
    isLoading: false,
  }),
}));
// Inert on purpose: nothing in this file navigates. Sign-out's navigation is pinned in
// user-menu.test.tsx, against a hoisted spy.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

import { AppBar } from "./app-bar";

function renderBar(action?: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AppBar action={action} />
    </QueryClientProvider>,
  );
}

describe("the app bar", () => {
  it("puts the brand on the left as a link home", () => {
    renderBar();

    expect(screen.getByRole("link", { name: "Evaluators home - My documents" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("shows a page's own action in the bar", () => {
    renderBar(<button type="button">Upload record</button>);

    expect(screen.getByRole("button", { name: "Upload record" })).toBeInTheDocument();
  });

  it("carries the user menu", () => {
    // No action passed, so the menu trigger is the only button and cannot be confused with one.
    renderBar();

    expect(screen.getByRole("button", { name: /Sam Reviewer/ })).toBeInTheDocument();
  });
});
