/**
 * The menu label and the trigger read the same field, so they have to agree on what "absent" means.
 *
 * They did not. The trigger uses `user?.name || user?.email`, which treats an empty string as
 * missing and falls through to the email; the label used `user?.name ?? "Signed in"`, which treats
 * it as present and rendered an empty bold line. One open menu, two answers for one field.
 *
 * This file covers that one divergence only. The rest of UserMenu - the initials, the admin gate,
 * sign out - is Phase 5 Task 8's components/app PR.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { CurrentUser } from "@/lib/types";

// Mutable so a test can change the user BETWEEN renders, which is the only way to reach both arms.
const current: { user: Partial<CurrentUser> } = { user: {} };

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: current.user, isLoading: false }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn().mockResolvedValue({}) }));

import { UserMenu } from "./user-menu";

function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** Radix renders the menu content only once the trigger is activated. */
async function openMenu() {
  const user = userEvent.setup();
  render(<UserMenu />, { wrapper: Wrapper });
  await user.click(screen.getByRole("button"));
  return screen.getByRole("menu");
}

beforeEach(() => {
  current.user = {};
});

describe("the user menu's name label", () => {
  it("says the reviewer is signed in when their name is saved as an empty string", async () => {
    current.user = { name: "", email: "reviewer@example.test" };

    const menu = await openMenu();

    // The whole point: `??` would keep the empty string and render a blank bold line here, while
    // the trigger beside it fell through to the email. Both now agree the name is absent.
    expect(within(menu).getByText("Signed in")).toBeInTheDocument();
    expect(within(menu).getByText("reviewer@example.test")).toBeInTheDocument();
  });

  it("shows the reviewer's actual name when they have one", async () => {
    // The decoy. Without it, the test above passes with the whole expression replaced by the
    // literal "Signed in" - it would pin the fallback and nothing else.
    current.user = { name: "Sam Reviewer", email: "reviewer@example.test" };

    const menu = await openMenu();

    expect(within(menu).getByText("Sam Reviewer")).toBeInTheDocument();
    expect(within(menu).queryByText("Signed in")).toBeNull();
  });

  it("says the reviewer is signed in when their name is only spaces", async () => {
    // THE CASE THE FIRST FIX MISSED. `""` is falsy so `||` already handled it; `"   "` is TRUTHY,
    // so the empty-string fix left this rendering three spaces in a bold line. The fixture above
    // could not express it - it distinguishes the value under test from a real name and leaves it
    // indistinguishable from the NEIGHBOURING falsy-looking value, which is the same shape as the
    // sort-fixture gap on #355 one level out.
    current.user = { name: "   ", email: "reviewer@example.test" };

    const menu = await openMenu();

    expect(within(menu).getByText("Signed in")).toBeInTheDocument();
  });
});

describe("the user menu's trigger", () => {
  it("falls back to the email on the trigger when the name is only spaces", async () => {
    // The SECOND site. `displayName` at user-menu.tsx:41 reads the same field and needed the same
    // trim, so this is a separate test rather than another assertion on the one above: a probe
    // that reverts one site should name the site it broke.
    //
    // Asserted BEFORE the menu opens, because the open menu also contains the email and an
    // unscoped assertion would pass on the menu's copy while the trigger still showed spaces.
    current.user = { name: "   ", email: "reviewer@example.test" };
    render(<UserMenu />, { wrapper: Wrapper });

    const trigger = screen.getByRole("button");

    // textContent rather than getByText: Testing Library normalises whitespace, so a trigger
    // rendering "   " and one rendering "" are indistinguishable through the usual queries.
    expect(trigger.textContent).toContain("reviewer@example.test");
  });
});
