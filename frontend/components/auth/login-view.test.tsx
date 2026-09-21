/**
 * Which form the auth entry point shows, and why.
 *
 * `LoginView` makes four decisions from two query parameters and the signed-in user, and none of
 * them had a test. The interesting ones are not the happy paths - they are the combinations a
 * reset link produces when it is stale, truncated, or hand-edited.
 *
 * ANCHORS ARE HEADINGS, AND ASSERTIONS ARE ROLE-SCOPED ON PURPOSE. Each view renders its own
 * AuthShell, whose title is an <h1>, so no child form needs stubbing - and stubbing them is what
 * produced three "the stub was too inert to observe the wiring" defects in Task 4. The scoping
 * matters too: "Sign in" and "Create an account" are each BOTH a heading and a button somewhere in
 * this flow, so an unscoped getByText would match the wrong element and still go green.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CurrentUser } from "@/lib/types";

// Backed by a REAL URLSearchParams rather than a hand-rolled { get }. A stub would have to
// reproduce `get` returning null for an absent key, which is the exact semantic the `token ?
// "reset" : "signin"` fallback turns on - a stub that returned undefined would test the stub.
const query = { params: new URLSearchParams() };
const replace = vi.fn();
const current: { user: Partial<CurrentUser> | undefined } = { user: undefined };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => query.params,
}));
vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: current.user, isLoading: false }),
}));
vi.mock("@/hooks/use-auth", () => ({
  useLogin: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegister: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useForgotPassword: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResetPassword: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { LoginView } from "@/components/auth/login-view";

const SIGN_IN = "Sign in";
const REGISTER = "Create an account";
// "Reset your password", NOT "Check your email". ForgotForm has two states and the second one is
// what a search for `AuthShell title=` finds first, because this one is formatted across lines and
// a line-oriented grep cannot see it. Getting this wrong is a test that looks for the form's
// AFTER-SUBMIT heading on a form nobody has submitted.
const FORGOT = "Reset your password";
const RESET = "Set a new password";
const EXPIRED = "Link expired";

/** Renders at a URL. `search` is written the way it appears after the "?" in a real link. */
function visit(search: string) {
  query.params = new URLSearchParams(search);
  render(<LoginView />);
}

function headingIs(title: string) {
  return screen.getByRole("heading", { name: title });
}

beforeEach(() => {
  replace.mockReset();
  current.user = undefined;
  query.params = new URLSearchParams();
});

describe("which form the auth entry point shows", () => {
  it("lands on sign-in when the URL says nothing", () => {
    visit("");
    expect(headingIs(SIGN_IN)).toBeInTheDocument();
  });

  it("opens the reset form when a link carries a token", () => {
    visit("token=abc123");
    expect(headingIs(RESET)).toBeInTheDocument();
  });

  it("says the link expired when the reset view arrives with no token", () => {
    // The `token ?? ""` arm. ResetForm has its own `if (!token)` branch, which is what makes the
    // empty string observable rather than silently rendering an unusable form.
    visit("view=reset");
    expect(headingIs(EXPIRED)).toBeInTheDocument();
  });

  it("offers a way back to sign-in from a dead reset link", () => {
    // Caught by the coverage run, not by the plan: three tests above render the reset view and
    // none of them leaves it, so ResetForm's onSignIn was the one arrow of the seven still at zero
    // hits. The guarantee is real rather than bookkeeping - a reviewer who follows an expired link
    // has no other way out of this screen.
    visit("view=reset");
    expect(headingIs(EXPIRED)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(headingIs(SIGN_IN)).toBeInTheDocument();
  });

  it("falls back to the link's own view when the view parameter is not one it knows", () => {
    // THE DECOY - and the fixture MUST carry a token, which is not obvious and was wrong first.
    //
    // The intuitive version of this test is `?view=dashboard` with no token, asserting sign-in.
    // MEASURED: that version is a NO-OP. Delete the whitelist entirely and `view` becomes
    // "dashboard", which matches none of the three `if` branches, so the final
    // `return <SignInForm />` catch-all renders sign-in anyway. Two mechanisms agreeing, which is
    // the shape that makes a green test prove nothing - the same defect as the paging test in #344
    // and the search handler in #355.
    //
    // A token makes them disagree. The whitelist rejects "dashboard" and falls back to the TOKEN's
    // implied view, so the reset form renders; a pass-through would carry "dashboard" past all
    // three branches to the sign-in catch-all.
    visit("token=abc123&view=dashboard");
    expect(headingIs(RESET)).toBeInTheDocument();
  });

  it("prefers an explicit view over the one the token implies", () => {
    // Both inputs are present and they disagree. Pinned because reading the code it could plausibly
    // go either way, and a stale query string on a reset link is how it happens in practice.
    visit("token=abc123&view=register");
    expect(headingIs(REGISTER)).toBeInTheDocument();
  });
});

describe("a reviewer who is already signed in", () => {
  it("is sent to the app rather than left on the sign-in form", async () => {
    current.user = { email: "reviewer@example.test" };
    visit("");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("is not redirected when nobody is signed in", async () => {
    visit("");
    // An absence assertion is worth nothing unless the channel is live, and the test above is what
    // proves this same spy fires. Waiting first, because asserting "not called" synchronously would
    // pass simply by running before the effect.
    await waitFor(() => expect(headingIs(SIGN_IN)).toBeInTheDocument());
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("moving between the forms", () => {
  it("goes to register and to forgot, and back to sign-in from each", () => {
    visit("");

    // Role-scoped throughout: on the sign-in view "Create an account" is a BUTTON, and on the
    // register view it is the HEADING. An unscoped query matches both and proves nothing moved.
    fireEvent.click(screen.getByRole("button", { name: REGISTER }));
    expect(headingIs(REGISTER)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: SIGN_IN }));
    expect(headingIs(SIGN_IN)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    expect(headingIs(FORGOT)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(headingIs(SIGN_IN)).toBeInTheDocument();
  });
});
