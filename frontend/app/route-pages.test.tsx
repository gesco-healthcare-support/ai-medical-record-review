/**
 * The six route pages. Each is one function of composition, so each test pins that the page puts
 * the right pieces on screen - and for three of them, the one thing that is not just composition.
 *
 * DEPOSITIONS AND DIAGNOSTICS ARE THE SAME PAGE apart from the config they hand the bundle client.
 * A test asserting "the bundle client rendered" passes with the two swapped, so those tests check
 * WHICH config arrives, by identity rather than by a label that could match either.
 *
 * THE RECORD PAGE IS ASYNC and awaits its `params` - Next 15 made them a Promise. Its test calls it
 * the way Next does and checks that the id in the address is the id the editor receives.
 *
 * THE LOGIN PAGE'S <Suspense> IS NOT PINNED HERE, and cannot be. Next requires it around a component
 * that reads `useSearchParams`, and it enforces that at `next build`, not at render - so CI's build
 * is its guard, and its probe here is DECLARED a NO-OP rather than passed off as covered.
 *
 * Children are stubbed: each has its own test file, and what is under test here is only the wiring.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Read through arrows, so the hoisted factories touch them only when a stub actually renders.
const bundleClient = vi.fn();
const reviewClient = vi.fn();

vi.mock("@/components/app/app-bar", () => ({ AppBar: () => <header data-testid="app-bar" /> }));
vi.mock("@/components/app/back-link", () => ({
  BackLink: () => <a data-testid="back-link" href="/" />,
}));
vi.mock("@/components/documents/documents-view", () => ({
  DocumentsView: () => <div data-testid="documents-view" />,
}));
vi.mock("@/components/admin/admin-view", () => ({
  AdminView: () => <div data-testid="admin-view" />,
}));
vi.mock("@/components/auth/login-view", () => ({
  LoginView: () => <div data-testid="login-view" />,
}));
vi.mock("@/components/bundle/bundle-page-client", () => ({
  BundlePageClient: (props: unknown) => {
    bundleClient(props);
    return <div data-testid="bundle-client" />;
  },
}));
vi.mock("@/components/review/review-page-client", () => ({
  ReviewPageClient: (props: unknown) => {
    reviewClient(props);
    return <div data-testid="review-client" />;
  },
}));

// The configs are REAL. Comparing against the real objects is what makes a swap detectable.
import { DEPOSITIONS, DIAGNOSTIC_OPERATIVE } from "@/lib/bundle-api";
import AdminPage from "@/app/admin/page";
import DepositionsPage from "@/app/depositions/page";
import DiagnosticsPage from "@/app/diagnostics/page";
import LoginPage from "@/app/login/page";
import HomePage from "@/app/page";
import RecordPage from "@/app/records/[id]/page";

beforeEach(() => {
  bundleClient.mockReset();
  reviewClient.mockReset();
});

/** The config the bundle client was last rendered with. */
function configPassed() {
  return (bundleClient.mock.lastCall?.[0] as { config: unknown } | undefined)?.config;
}

describe("the route pages", () => {
  it("the home page renders the app bar and My documents", () => {
    render(<HomePage />);

    expect(screen.getByTestId("app-bar")).toBeInTheDocument();
    expect(screen.getByTestId("documents-view")).toBeInTheDocument();
  });

  it("the login page renders the sign-in flow", () => {
    render(<LoginPage />);

    expect(screen.getByTestId("login-view")).toBeInTheDocument();
  });

  it("the admin page has the app bar, a way back, and the console", () => {
    render(<AdminPage />);

    expect(screen.getByTestId("app-bar")).toBeInTheDocument();
    expect(screen.getByTestId("back-link")).toBeInTheDocument();
    expect(screen.getByTestId("admin-view")).toBeInTheDocument();
  });

  it("the depositions page builds the depositions bundle", () => {
    render(<DepositionsPage />);

    expect(screen.getByTestId("app-bar")).toBeInTheDocument();
    expect(screen.getByTestId("back-link")).toBeInTheDocument();
    expect(configPassed()).toBe(DEPOSITIONS);
  });

  it("the diagnostics page builds the diagnostic and operative bundle", () => {
    render(<DiagnosticsPage />);

    expect(screen.getByTestId("app-bar")).toBeInTheDocument();
    expect(screen.getByTestId("back-link")).toBeInTheDocument();
    expect(configPassed()).toBe(DIAGNOSTIC_OPERATIVE);
  });

  it("a record page opens the record named in its address", async () => {
    // An id no constant in the code could produce by accident.
    render(await RecordPage({ params: Promise.resolve({ id: "rec-7f3a" }) }));

    expect(screen.getByTestId("app-bar")).toBeInTheDocument();
    expect(reviewClient.mock.lastCall?.[0]).toEqual({ documentId: "rec-7f3a" });
  });
});
