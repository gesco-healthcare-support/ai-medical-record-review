import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const combine = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({ useCombineReceivedFiles: () => combine }));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { ReceivedFilesDialog } from "@/components/documents/received-files-dialog";

const pdf = (name: string) => new File([new Uint8Array([1])], name, { type: "application/pdf" });
const other = (name: string) =>
  new File([new Uint8Array([1])], name, { type: "application/msword" });

afterEach(() => {
  vi.clearAllMocks();
  combine.isPending = false;
});

function addFiles(files: File[]) {
  fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files } });
}

function listed(): string[] {
  return within(screen.getByRole("list"))
    .getAllByRole("listitem")
    .map((li) => li.querySelector(".truncate")!.textContent ?? "");
}

async function fillPatient(
  user: ReturnType<typeof userEvent.setup>,
  { id = true }: { id?: boolean } = {},
) {
  await user.type(screen.getByLabelText("Patient last name"), "Synthetic");
  await user.type(screen.getByLabelText("Patient first name"), "Patient");
  if (id) await user.type(screen.getByLabelText("Patient ID"), "801234");
  fireEvent.change(screen.getByLabelText("Date received"), { target: { value: "2026-11-03" } });
  const appointment = screen.getByLabelText("Appointment date");
  fireEvent.change(appointment, { target: { value: "2026-12-01" } });
  await user.selectOptions(screen.getByLabelText("Records from"), "applicant");
}

describe("ReceivedFilesDialog ordering", () => {
  it("lists the files in the office's order, by the kind their names suggest", async () => {
    // DEMONSTRATES the office's guideline: advocacy letter, declaration, medical index, medical
    // records, proof of service, internal email, envelope - whatever order they were picked in.
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([
      pdf("hospital records.pdf"),
      pdf("envelope.pdf"),
      pdf("POS.pdf"),
      pdf("advocacy letter.pdf"),
      pdf("email.pdf"),
      pdf("DEC.pdf"),
      pdf("medical index.pdf"),
      pdf("clinic records.pdf"),
    ]);

    expect(listed()).toEqual([
      "advocacy letter.pdf",
      "DEC.pdf",
      "medical index.pdf",
      "hospital records.pdf",
      "clinic records.pdf",
      "POS.pdf",
      "email.pdf",
      "envelope.pdf",
    ]);
    expect(screen.getByLabelText("What DEC.pdf is")).toHaveValue("declaration");
    expect(screen.getByLabelText("What medical index.pdf is")).toHaveValue("medical_index");
  });

  it("moves a file into its place when its kind is changed", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf"), pdf("scan2.pdf")]);

    await user.selectOptions(screen.getByLabelText("What scan2.pdf is"), "advocacy_letter");

    expect(listed()).toEqual(["scan2.pdf", "records.pdf"]);
  });

  it("moves a file up or down by hand, and removes one", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a records.pdf"), pdf("b records.pdf"), pdf("c records.pdf")]);
    expect(screen.getByRole("button", { name: "Move a records.pdf up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move c records.pdf down" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Move c records.pdf up" }));
    expect(listed()).toEqual(["a records.pdf", "c records.pdf", "b records.pdf"]);

    await user.click(screen.getByRole("button", { name: "Move a records.pdf down" }));
    expect(listed()).toEqual(["c records.pdf", "a records.pdf", "b records.pdf"]);

    await user.click(screen.getByRole("button", { name: "Remove a records.pdf" }));
    expect(listed()).toEqual(["c records.pdf", "b records.pdf"]);
  });
});

describe("ReceivedFilesDialog sending", () => {
  it("sends the files in the order listed, with their kinds and the patient's details", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 6, patient_records: 0 });
    render(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    const records = pdf("records.pdf");
    const letter = pdf("letter.pdf");
    addFiles([records, letter]);
    await fillPatient(user);

    await user.click(screen.getByRole("button", { name: "Combine & upload" }));

    await waitFor(() => expect(combine.mutateAsync).toHaveBeenCalledTimes(1));
    expect(combine.mutateAsync).toHaveBeenCalledWith({
      lastName: "Synthetic",
      firstName: "Patient",
      patientId: "801234",
      dateReceived: "2026-11-03",
      appointmentDate: "2026-12-01",
      recordsFrom: "applicant",
      files: [
        { file: letter, kind: "advocacy_letter" },
        { file: records, kind: "records" },
      ],
      owner: null,
    });
    expect(vi.mocked(toast).success).toHaveBeenCalledWith(
      "One record made from 2 files, 6 pages.",
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("reads the patient ID from the files' names when they agree", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("Synthetic 801234 email.pdf"), pdf("Synthetic-801234-records.pdf")]);
    await fillPatient(user, { id: false });

    expect(screen.getByLabelText("Patient ID")).toHaveValue("801234");
    await user.click(screen.getByRole("button", { name: "Combine & upload" }));

    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ patientId: "801234" }),
      ),
    );
  });

  it("asks for every detail before it can send", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    const send = () => screen.getByRole("button", { name: "Combine & upload" });
    expect(send()).toBeDisabled();

    addFiles([pdf("records.pdf")]);
    expect(send()).toBeDisabled();
    await user.type(screen.getByLabelText("Patient last name"), "Synthetic");
    await user.type(screen.getByLabelText("Patient first name"), "Patient");
    await user.type(screen.getByLabelText("Patient ID"), "801234");
    expect(send()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Date received"), { target: { value: "2026-11-03" } });
    expect(send()).toBeDisabled();
    const appointment = screen.getByLabelText("Appointment date");
    fireEvent.change(appointment, { target: { value: "2026-12-01" } });
    expect(send()).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Records from"), "copy_service");
    expect(send()).toBeEnabled();

    await user.clear(screen.getByLabelText("Patient ID"));
    await user.type(screen.getByLabelText("Patient ID"), "x".repeat(40));
    expect(send()).toBeDisabled();
    expect(screen.getByText(/at most 32 letters and digits/)).toBeInTheDocument();
  });

  it("names the reviewer and sends the record to them when an admin uploads for one", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 1, patient_records: 2 });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} owner={{ id: 7, name: "Brian" }} />);
    expect(
      screen.getByRole("heading", { name: "Upload received files for Brian" }),
    ).toBeInTheDocument();
    addFiles([pdf("records.pdf")]);
    await fillPatient(user);

    await user.click(screen.getByRole("button", { name: "Combine & upload for Brian" }));

    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ owner: 7 })),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith(
      expect.stringMatching(/^One record made from 1 file, 1 pages, in Brian's records\. /),
    );
  });

  it("toasts a humanized message when combining fails, and keeps what was staged", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await fillPatient(user);

    await user.click(screen.getByRole("button", { name: "Combine & upload" }));

    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
    expect(listed()).toEqual(["records.pdf"]);
  });

  it("shows that it is combining while the upload runs", () => {
    combine.isPending = true;
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Combining..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });
});

describe("ReceivedFilesDialog file handling", () => {
  it("says so when a picked file is not a PDF, and keeps the PDFs", async () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a.pdf"), other("b.doc"), other("c.doc"), pdf("d.pdf")]);

    expect(vi.mocked(toast).error).toHaveBeenCalledWith(
      "Only PDF files can be combined. 2 files were not added.",
    );
    expect(listed()).toEqual(["a.pdf", "d.pdf"]);
  });

  it("says one file when one is not a PDF", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([other("b.doc")]);
    expect(vi.mocked(toast).error).toHaveBeenCalledWith(
      "Only PDF files can be combined. One file was not added.",
    );
  });

  it("clears everything staged on Cancel", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const { rerender } = render(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    addFiles([pdf("a.pdf")]);
    await user.type(screen.getByLabelText("Patient last name"), "Synthetic");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    rerender(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    expect(screen.getByText("No files yet.")).toBeInTheDocument();
    expect(screen.getByLabelText("Patient last name")).toHaveValue("");
  });

  it("clears everything staged when dismissed with Escape too", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const { rerender } = render(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    addFiles([pdf("a.pdf")]);

    await user.keyboard("{Escape}");

    expect(onOpenChange).toHaveBeenCalledWith(false);
    rerender(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    expect(screen.getByText("No files yet.")).toBeInTheDocument();
  });
});
