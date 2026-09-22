/**
 * The user menu: who it says you are, what it offers you, and signing out.
 *
 * It began as one divergence. The menu label and the trigger read the same field and have to agree
 * on what "absent" means; they did not. The trigger used `user?.name || user?.email`, which treats
 * an empty string as missing and falls through to the email, while the label used
 * `user?.name ?? "Signed in"`, which kept it and rendered an empty bold line. The first two
 * describe blocks below are that fix and its follow-up for a name that is only spaces.
 *
 * The rest is Phase 5 Task 8.2: signing out, the admin gate, and the loading state.
 *
 * SIGN-OUT IS THE PART WORTH READING CAREFULLY, and `queryClient.clear()` is the line that matters.
 * Query keys here are not scoped to a user, and `useCurrentUser` treats its data as fresh for five
 * minutes, so a sign-out that left the cache in place would hand the login screen a reviewer who
 * still looks signed in - over the previous reviewer's cached document list. The fixture therefore
 * FILLS the cache before signing out. "The cache is empty afterwards" is also true of a cache that
 * was never filled, and would pass with the clear deleted.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CurrentUser } from "@/lib/types";

// Mutable so a test can change the user BETWEEN renders, which is the only way to reach both arms.
// `undefined` is the loading state: what `useQuery` returns before its first response arrives.
const current: { user: Partial<CurrentUser> | undefined } = { user: {} };
// Hoisted rather than `useRouter: () => ({ push: vi.fn() })`, which is what this file had. That
// shape builds a NEW spy on every call, so no test could ever observe where sign-out navigates.
const push = vi.fn();

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: current.user, isLoading: current.user === undefined }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
// Mocked wholesale, and it has to stay that way: the real `apiFetch` answers a 401 with
// `window.location.assign("/login")`, which jsdom does not implement. It also means `ApiError` is
// not importable in this file, which is why the failure test below rejects with a plain Error.
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn().mockResolvedValue({}) }));

import { apiFetch } from "@/lib/api";
import { UserMenu } from "./user-menu";

/** Renders under a QueryClient the TEST owns, so a test can fill the cache and then inspect it. */
function renderMenu() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <UserMenu />
    </QueryClientProvider>,
  );
  return { client };
}

/** Radix renders the menu content only once the trigger is activated. */
async function openMenu() {
  const user = userEvent.setup();
  const { client } = renderMenu();
  await user.click(screen.getByRole("button"));
  return { menu: screen.getByRole("menu"), client, user };
}

/**
 * Leaves the cache the way a signed-in session does: the identity, AND data that is not about
 * identity at all. The second entry is load-bearing - a clear narrowed to `["current-user"]` would
 * still "sign the reviewer out" while leaving another reviewer's records behind, and against an
 * identity-only cache it would pass.
 */
function fillCache(client: QueryClient) {
  client.setQueryData(["current-user"], { email: "reviewer@example.test" });
  client.setQueryData(["documents"], [{ id: "d1", original_filename: "synthetic-record.pdf" }]);
  expect(client.getQueryCache().getAll()).toHaveLength(2);
}

beforeEach(() => {
  current.user = {};
  push.mockReset();
  vi.mocked(apiFetch).mockClear();
});

describe("the user menu's name label", () => {
  it("says the reviewer is signed in when their name is saved as an empty string", async () => {
    current.user = { name: "", email: "reviewer@example.test" };

    const { menu } = await openMenu();

    // The whole point: `??` would keep the empty string and render a blank bold line here, while
    // the trigger beside it fell through to the email. Both now agree the name is absent.
    expect(within(menu).getByText("Signed in")).toBeInTheDocument();
    expect(within(menu).getByText("reviewer@example.test")).toBeInTheDocument();
  });

  it("shows the reviewer's actual name when they have one", async () => {
    // The decoy. Without it, the test above passes with the whole expression replaced by the
    // literal "Signed in" - it would pin the fallback and nothing else.
    current.user = { name: "Sam Reviewer", email: "reviewer@example.test" };

    const { menu } = await openMenu();

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

    const { menu } = await openMenu();

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
    renderMenu();

    const trigger = screen.getByRole("button");

    // textContent rather than getByText: Testing Library normalises whitespace, so a trigger
    // rendering "   " and one rendering "" are indistinguishable through the usual queries.
    expect(trigger.textContent).toContain("reviewer@example.test");
  });

  it("shows placeholders while the reviewer's details are still loading", async () => {
    // Every signed-in page shows this for a moment on load, before `/users/me` answers. Read
    // BEFORE opening: an open Radix menu is modal and hides the trigger from role queries.
    current.user = undefined;
    const user = userEvent.setup();
    renderMenu();

    const trigger = screen.getByRole("button");
    expect(trigger.textContent).toContain("?");
    expect(trigger.textContent).toContain("Account");

    await user.click(trigger);
    const menu = screen.getByRole("menu");

    expect(within(menu).getByText("Signed in")).toBeInTheDocument();
    // Not flashing Admin at someone whose role has not loaded yet. The label above is the positive
    // that makes this absence mean something.
    expect(within(menu).queryByRole("menuitem", { name: "Admin" })).toBeNull();
    // The email line's `: null` arm also runs here and is deliberately NOT asserted: its only
    // effect is an empty <span> nobody can see, so asserting on it would pin markup, not behaviour.
  });
});

describe("what the menu offers", () => {
  it("offers the Admin page to an admin", async () => {
    current.user = { name: "Sam Reviewer", email: "reviewer@example.test", is_superuser: true };

    const { menu } = await openMenu();

    expect(within(menu).getByRole("menuitem", { name: "Admin" })).toHaveAttribute("href", "/admin");
  });

  it("does not offer the Admin page to a reviewer", async () => {
    // The other half of the gate, and the half with teeth: the test above passes with the gate
    // replaced by `true`. `false` explicitly, because that is what the API sends - not `undefined`.
    current.user = { name: "Sam Reviewer", email: "reviewer@example.test", is_superuser: false };

    const { menu } = await openMenu();

    // The positive first. "No Admin item" is also true of a menu that rendered no items at all.
    expect(within(menu).getByRole("menuitem", { name: "Depositions" })).toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "Admin" })).toBeNull();
  });
});

describe("signing out", () => {
  it(
    "signs out: tells the server, empties every cached query, and goes to the login screen",
    async () => {
      current.user = { name: "Sam Reviewer", email: "reviewer@example.test" };
      const { menu, client, user } = await openMenu();
      fillCache(client);

      await user.click(within(menu).getByRole("menuitem", { name: "Sign out" }));

      // Messages on every assertion, because four mutations fail THIS test and each must be seen to
      // fail it at its own line rather than somewhere downstream.
      expect(apiFetch, "the logout request was not a POST to /auth/logout").toHaveBeenCalledWith(
        "/auth/logout",
        { method: "POST" },
      );
      // Navigation is last in `signOut`, so once it has happened the clear has too.
      await waitFor(() =>
        expect(push, "sign-out did not go to the login screen").toHaveBeenCalledWith("/login"),
      );
      expect(
        client.getQueryData(["current-user"]),
        "the signed-in identity survived sign-out",
      ).toBeUndefined();
      expect(
        client.getQueryData(["documents"]),
        "the document list survived sign-out",
      ).toBeUndefined();
    },
  );

  it("still signs out locally when the logout request fails", async () => {
    // Once, never `mockRejectedValue`: `clearAllMocks` does not reset an implementation, and a
    // sticky rejection in #355 quietly ran six later tests down the error path.
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("network down"));
    current.user = { name: "Sam Reviewer", email: "reviewer@example.test" };
    const { menu, client, user } = await openMenu();
    fillCache(client);

    await user.click(within(menu).getByRole("menuitem", { name: "Sign out" }));

    // A reviewer whose network drops at the wrong moment must not be left looking signed in.
    await waitFor(() => expect(push).toHaveBeenCalledWith("/login"));
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
});
