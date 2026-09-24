import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/review-api", () => ({ saveHeader: vi.fn(), extractHeader: vi.fn() }));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { extractHeader, saveHeader } from "@/lib/review-api";
import { HeaderBar } from "@/components/review/header-bar";

const mockToast = vi.mocked(toast);

afterEach(() => vi.clearAllMocks());

describe("HeaderBar error handling", () => {
  it("toasts a humanized 404 when auto-fill fails", async () => {
    const user = userEvent.setup();
    vi.mocked(extractHeader).mockRejectedValue(new ApiError("not found", 404));
    render(<HeaderBar documentId="d1" header={null} onSaved={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Auto-fill" }));
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(expect.stringMatching(/no longer available/i)),
    );
  });

  it("toasts a humanized network error when saving the header fails", async () => {
    const user = userEvent.setup();
    vi.mocked(saveHeader).mockRejectedValue(new ApiError("network", 0));
    render(<HeaderBar documentId="d1" header={null} onSaved={vi.fn()} />);
    await user.type(screen.getByPlaceholderText("First"), "Jane"); // dirties the form -> Save enables
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
  });
});

describe("HeaderBar fields", () => {
  it("saves every field with the value typed into it", async () => {
    // Asserted on the PAYLOAD rather than on the boxes, because two of these share the placeholder
    // MM/DD/YYYY: a DOB wired to letter_date looks identical on screen and is visible only in what
    // gets sent. The reviewer would find out when the delivered letter carried the wrong date.
    const user = userEvent.setup();
    vi.mocked(saveHeader).mockResolvedValue(undefined);
    render(<HeaderBar documentId="d1" doctors={["Dr Vasquez"]} header={null} onSaved={vi.fn()} />);

    await user.type(screen.getByLabelText("Last name"), "Roe");
    await user.type(screen.getByLabelText("DOB"), "04/05/1980");
    // Short, DISTINCT values: the payload check needs each box to carry its own value, not a long
    // one - every keystroke re-renders the whole bar, and long strings are what timed this out.
    await user.type(screen.getByLabelText("Attorney"), "Reyes");
    await user.type(screen.getByLabelText("Law firm"), "Acme");
    await user.selectOptions(screen.getByLabelText("Letter"), "advocacy");
    await user.type(screen.getByLabelText("Letter date"), "06/07/2026");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveHeader).toHaveBeenCalled());
    expect(vi.mocked(saveHeader).mock.calls[0][1]).toMatchObject({
      patient_last_name: "Roe",
      patient_dob: "04/05/1980",
      attorney_name: "Reyes",
      law_firm: "Acme",
      letter_type: "advocacy",
      letter_date: "06/07/2026",
    });
  });

  // Each box writes through its own onChange. One that trimmed on every keystroke would turn "a b" into
  // "ab" - the space is gone before the "b" arrives - so a firm typed as "Acme LLP" would reach the
  // letter as "AcmeLLP". Only typing exposes it: a pasted value survives a trim of its ends. One test
  // per box, so each has its own time budget and a failure names the box.
  it.each(["First name", "Last name", "Attorney", "Law firm"])(
    "keeps a space typed into %s",
    async (label) => {
      const user = userEvent.setup();
      render(<HeaderBar documentId="d1" doctors={[]} header={null} onSaved={vi.fn()} />);
      const box = screen.getByLabelText(label);
      await user.type(box, "a b");
      expect(box).toHaveValue("a b");
    },
  );
});

describe("HeaderBar persistence", () => {
  const DETECTED = {
    patient_first_name: "Jane",
    patient_last_name: "Roe",
    patient_dob: "01/02/1990",
    law_firm: "Acme LLP",
    attorney_name: "",
    doctor: "",
    letter_type: "",
    letter_date: "",
    pages_received: "",
  };

  it("persists on Auto-fill: reports the detected header via onSaved without a separate Save", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    vi.mocked(extractHeader).mockResolvedValue(DETECTED);
    render(<HeaderBar documentId="d1" header={null} onSaved={onSaved} />);

    await user.click(screen.getByRole("button", { name: "Auto-fill" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(DETECTED));
    expect(mockToast.success).toHaveBeenCalledWith(expect.stringMatching(/detected and saved/i));
    expect(saveHeader).not.toHaveBeenCalled(); // detect alone persists; no manual Save needed
  });

  it("labels the button Re-detect once a header value is present", () => {
    render(
      <HeaderBar
        documentId="d1"
        header={{ ...DETECTED, patient_last_name: "", patient_dob: "", law_firm: "" }}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Re-detect" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Auto-fill" })).toBeNull();
  });
});

describe("HeaderBar doctor selection", () => {
  const FILLED = {
    patient_first_name: "Jane",
    patient_last_name: "Roe",
    patient_dob: "01/02/1990",
    law_firm: "Acme LLP",
    attorney_name: "",
    doctor: "",
    letter_type: "",
    letter_date: "",
    pages_received: "",
  };

  it("offers the served doctors", () => {
    render(
      <HeaderBar
        documentId="d1"
        header={FILLED}
        doctors={["Falkinstein", "Pelton"]}
        onSaved={vi.fn()}
      />,
    );
    const select = screen.getByLabelText("Doctor") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(["", "Falkinstein", "Pelton"]);
  });

  it("keeps showing a stored doctor the served list does not contain", () => {
    // DEMONSTRATES the trap. The list arrives with the record, so it is empty on first paint - and
    // a <select> whose value is absent from its options renders the FIRST option instead. The next
    // save would then write the record's doctor away without anyone touching the field.
    render(
      <HeaderBar
        documentId="d1"
        header={{ ...FILLED, doctor: "Retired Doctor" }}
        doctors={[]}
        onSaved={vi.fn()}
      />,
    );
    const select = screen.getByLabelText("Doctor") as HTMLSelectElement;
    expect(select.value).toBe("Retired Doctor");
  });

  it("saves the doctor the reviewer picked", async () => {
    const user = userEvent.setup();
    vi.mocked(saveHeader).mockResolvedValue(undefined);
    render(
      <HeaderBar
        documentId="d1"
        header={FILLED}
        doctors={["Falkinstein", "Pelton"]}
        onSaved={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByLabelText("Doctor"), "Pelton");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(saveHeader).toHaveBeenCalledWith(
        "d1",
        expect.objectContaining({ doctor: "Pelton" }),
      ),
    );
  });

  it("sends the cover-sheet page count as typed, for the server to coerce", async () => {
    const user = userEvent.setup();
    vi.mocked(saveHeader).mockResolvedValue(undefined);
    render(<HeaderBar documentId="d1" header={FILLED} onSaved={vi.fn()} />);
    await user.type(screen.getByPlaceholderText("From cover sheet"), "418");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(saveHeader).toHaveBeenCalledWith(
        "d1",
        expect.objectContaining({ pages_received: "418" }),
      ),
    );
  });
});
