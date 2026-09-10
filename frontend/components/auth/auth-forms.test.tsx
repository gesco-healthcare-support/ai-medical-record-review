import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// One mutable rejection per mutation, so a test picks what the server (or the network) did.
const outcomes: Record<string, unknown> = {};
const call = (key: string) =>
  vi.fn(async () => {
    const outcome = outcomes[key];
    if (outcome) throw outcome;
    return null;
  });
const mutations = {
  login: call("login"),
  register: call("register"),
  forgot: call("forgot"),
  reset: call("reset"),
};
vi.mock("@/hooks/use-auth", () => ({
  useLogin: () => ({ mutateAsync: mutations.login, isPending: false }),
  useRegister: () => ({ mutateAsync: mutations.register, isPending: false }),
  useForgotPassword: () => ({ mutateAsync: mutations.forgot, isPending: false }),
  useResetPassword: () => ({ mutateAsync: mutations.reset, isPending: false }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

import { ApiError } from "@/lib/api";
import { ForgotForm } from "@/components/auth/forgot-form";
import { RegisterForm } from "@/components/auth/register-form";
import { ResetForm } from "@/components/auth/reset-form";
import { SignInForm } from "@/components/auth/sign-in-form";

const OFFLINE = new ApiError("network", 0);
const REACHED_THE_SERVER = new ApiError("Bad credentials", 400);
const OFFLINE_COPY = /couldn't reach the server/i;

/** These four forms had no tests at all.
 *
 *  Each writes its own failure copy rather than calling `humanizeError`, and correctly so - a 401
 *  here is a wrong password, not "your session has ended". The cost was that the copy also spoke
 *  for a failure that never reached the server, so the app blamed the reader for a dropped
 *  connection: wrong password, expired link, bad details, or an email that was never sent. */
describe("auth forms on a dropped connection", () => {
  beforeEach(() => {
    for (const key of Object.keys(outcomes)) delete outcomes[key];
    vi.clearAllMocks();
  });

  function fill(label: RegExp, value: string) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }

  describe("sign in", () => {
    const submit = () => {
      render(<SignInForm onRegister={vi.fn()} onForgot={vi.fn()} />);
      fill(/email/i, "a@b.com");
      fill(/password/i, "Password!1");
      fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    };

    it("says the server was unreachable instead of blaming the password", async () => {
      outcomes.login = OFFLINE;
      submit();
      await waitFor(() => expect(screen.getByText(OFFLINE_COPY)).toBeInTheDocument());
      expect(screen.queryByText(/check your email and password/i)).not.toBeInTheDocument();
    });

    it("still blames the credentials when the server actually answered", async () => {
      // A GUARD. `humanizeError` would render this 401/400 as "your session has ended", which is
      // why this page keeps its own copy - only the transport case is delegated.
      outcomes.login = REACHED_THE_SERVER;
      submit();
      await waitFor(() =>
        expect(screen.getByText(/check your email and password/i)).toBeInTheDocument(),
      );
    });
  });

  describe("reset password", () => {
    const submit = () => {
      render(<ResetForm token="tok" onSignIn={vi.fn()} />);
      fill(/new password/i, "Password!1");
      fill(/confirm password/i, "Password!1");
      fireEvent.click(screen.getByRole("button", { name: /update password/i }));
    };

    it("says the server was unreachable instead of condemning the link", async () => {
      outcomes.reset = OFFLINE;
      submit();
      await waitFor(() => expect(screen.getByText(OFFLINE_COPY)).toBeInTheDocument());
      // The link may be perfectly good; sending the reader to request another one wastes the trip.
      expect(screen.queryByText(/invalid or has expired/i)).not.toBeInTheDocument();
    });

    it("still condemns a link the server rejected", async () => {
      outcomes.reset = REACHED_THE_SERVER; // a spent or forged token is a 400
      submit();
      await waitFor(() => expect(screen.getByText(/invalid or has expired/i)).toBeInTheDocument());
    });
  });

  describe("register", () => {
    const submit = () => {
      render(<RegisterForm onSignIn={vi.fn()} />);
      fill(/full name/i, "A Reviewer");
      fill(/email/i, "a@b.com");
      fill(/^password$/i, "Password!1");
      fill(/confirm password/i, "Password!1");
      fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    };

    it("says the server was unreachable instead of blaming the details", async () => {
      outcomes.register = OFFLINE;
      submit();
      await waitFor(() => expect(screen.getByText(OFFLINE_COPY)).toBeInTheDocument());
    });

    it("still reports an email that is already taken", async () => {
      // A GUARD: the 400 branch predates this change and is the one message on these pages that
      // depends on the status, so it is the one most easily broken by adding another branch.
      outcomes.register = REACHED_THE_SERVER;
      submit();
      await waitFor(() =>
        expect(screen.getByText(/account with this email already exists/i)).toBeInTheDocument(),
      );
    });
  });

  describe("forgot password", () => {
    const submit = () => {
      render(<ForgotForm onSignIn={vi.fn()} />);
      fill(/email/i, "a@b.com");
      fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    };

    it("does not claim an email was sent when the request never left the machine", async () => {
      outcomes.forgot = OFFLINE;
      submit();
      await waitFor(() => expect(screen.getByText(OFFLINE_COPY)).toBeInTheDocument());
      expect(screen.queryByText(/check your email/i)).not.toBeInTheDocument();
    });

    it("keeps the enumeration protection for anything the server answered", async () => {
      // THE GUARD THAT MATTERS. A rejection that reached the server must still show the same
      // confirmation, or the presence of an account becomes observable from the response.
      outcomes.forgot = new ApiError("no such user", 404);
      submit();
      await waitFor(() => expect(screen.getByText(/check your email/i)).toBeInTheDocument());
      expect(screen.queryByText(OFFLINE_COPY)).not.toBeInTheDocument();
    });

    it("shows the same confirmation on success", async () => {
      submit();
      await waitFor(() => expect(screen.getByText(/check your email/i)).toBeInTheDocument());
    });
  });
});
