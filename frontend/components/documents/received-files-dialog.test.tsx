import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const combine = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({
  useCombineReceivedFiles: () => combine,
  useDoctors: () => ({ data: ["Alpha", "Beta"] }),
}));

const stage = vi.fn();
const unstage = vi.fn();
vi.mock("@/lib/documents-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/documents-api")>()),
  stageReceivedFile: (file: File) => stage(file),
  unstageReceivedFile: (token: string) => unstage(token),
}));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { ReceivedFilesDialog } from "@/components/documents/received-files-dialog";

const pdf = (name: string) => new File([new Uint8Array([1])], name, { type: "application/pdf" });
const other = (name: string) =>
  new File([new Uint8Array([1])], name, { type: "application/msword" });

/** What the server says about each file, by name; by default its first page says nothing more than
 *  "records" and it has 2 pages, so the name decides. */
let readings: Record<
  string,
  { kind?: string; pages?: number; check?: boolean; sha256?: string; before?: unknown[] }
> = {};

beforeEach(() => {
  readings = {};
  stage.mockImplementation(async (file: File) => ({
    token: `tok-${file.name}`,
    pages: readings[file.name]?.pages ?? 2,
    kind: readings[file.name]?.kind ?? "records",
    check: readings[file.name]?.check ?? false,
    sha256: readings[file.name]?.sha256 ?? `sha-${file.name}`,
    uploaded_before: readings[file.name]?.before ?? [],
  }));
  unstage.mockResolvedValue({ ok: true });
});

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
    .map((li) => li.querySelector("[data-file-name]")!.textContent ?? "");
}

/** The card of the file named `name`. */
function rowOf(name: string): HTMLElement {
  const file = within(screen.getByRole("list"))
    .getAllByRole("listitem")
    .find((li) => li.querySelector("[data-file-name]")?.textContent === name);
  if (!file) throw new Error(`no file ${name}`);
  return file;
}

/** Whether the file named `name` shows its pages and that the uploader confirmed it. */
function isConfirmed(name: string, pages: string): boolean {
  const row = within(rowOf(name));
  return row.queryByText(pages) !== null && row.queryByText("Confirmed") !== null;
}

/** No file listed yet: the list is gone and the drop zone asks for the files. */
function noFiles(): boolean {
  return (
    screen.queryByRole("list") === null &&
    screen.queryByText("Drag the file or files here, in any order, or") !== null
  );
}

async function allRead(count: number) {
  await waitFor(() => expect(stage).toHaveBeenCalledTimes(count));
  await waitFor(() => expect(screen.queryByText("Reading...")).toBeNull());
}

function deferredStage() {
  const handle: { answer: (value: unknown) => void } = { answer: () => undefined };
  stage.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        handle.answer = resolve;
      }),
  );
  return handle;
}

/** Confirm every file's kind, as the uploader must before the record can be made. */
async function confirmAll(user: ReturnType<typeof userEvent.setup>) {
  for (const button of screen.queryAllByRole("button", { name: /^Confirm .+ as / })) {
    await user.click(button);
  }
}

async function fillPatient(
  user: ReturnType<typeof userEvent.setup>,
  { id = true }: { id?: boolean } = {},
) {
  // Values set directly: typing them key by key is the slow part of every test here.
  fireEvent.change(screen.getByLabelText("Patient last name"), { target: { value: "Synthetic" } });
  fireEvent.change(screen.getByLabelText("Patient first name"), { target: { value: "Patient" } });
  if (id) fireEvent.change(screen.getByLabelText("Patient ID"), { target: { value: "801234" } });
  fireEvent.change(screen.getByLabelText("Date received"), { target: { value: "2026-11-03" } });
  await user.selectOptions(screen.getByLabelText("Records from"), "applicant");
  fillBilling();
}

/** The billing sheet's required fields: type of visit, appointment date, reviewed by, interpreter.
 *  Set directly, like the boxes above: a click per option is what pushes these tests past their
 *  time limit on a loaded machine. */
function fillBilling() {
  const set = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  set("Type of visit", "QME");
  set("Appointment date", "2026-12-01");
  set("Interpreter", "no");
}

describe("ReceivedFilesDialog ordering", () => {
  it("puts each file in its place by what its first page says, whatever its name", async () => {
    // DEMONSTRATES the office's request: the order does not depend on the uploader - files named
    // anything, added in any order, come out in the guideline's order once the server read them.
    readings = {
      "scan1.pdf": { kind: "proof_of_service" },
      "scan2.pdf": { kind: "records" },
      "scan3.pdf": { kind: "advocacy_letter" },
      "scan4.pdf": { kind: "email" },
      "scan5.pdf": { kind: "declaration" },
    };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles(["scan1.pdf", "scan2.pdf", "scan3.pdf", "scan4.pdf", "scan5.pdf"].map(pdf));

    await allRead(5);

    expect(listed()).toEqual(["scan3.pdf", "scan5.pdf", "scan2.pdf", "scan1.pdf", "scan4.pdf"]);
    expect(screen.getByLabelText("What scan5.pdf is")).toHaveValue("declaration");
  });

  it("uses the file's name only when the reading was unsure and said records", async () => {
    // A scan the server could not read: the name is the better guess, and the file is flagged.
    readings = {
      "hospital records.pdf": { check: true },
      "POS.pdf": { check: true },
      "medical index.pdf": { check: true },
    };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("hospital records.pdf"), pdf("POS.pdf"), pdf("medical index.pdf")]);

    await allRead(3);

    expect(listed()).toEqual(["medical index.pdf", "hospital records.pdf", "POS.pdf"]);
  });

  it("keeps a kind the uploader chose over what the page says", async () => {
    const user = userEvent.setup();
    const slow = deferredStage();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("scan.pdf"), pdf("records.pdf")]);

    await user.selectOptions(screen.getByLabelText("What scan.pdf is"), "declaration");
    await act(async () => slow.answer({ token: "t1", pages: 3, kind: "email" }));

    expect(screen.getByLabelText("What scan.pdf is")).toHaveValue("declaration");
    expect(isConfirmed("scan.pdf", "3 pages")).toBe(true);
  });

  it("moves a file into its place when its kind is changed", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf"), pdf("scan2.pdf")]);
    await allRead(2);

    await user.selectOptions(screen.getByLabelText("What scan2.pdf is"), "advocacy_letter");

    expect(listed()).toEqual(["scan2.pdf", "records.pdf"]);
  });

  it("moves a file up or down by hand, and removes one", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a records.pdf"), pdf("b records.pdf"), pdf("c records.pdf")]);
    await allRead(3);
    expect(screen.getByRole("button", { name: "Move a records.pdf up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move c records.pdf down" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Move c records.pdf up" }));
    expect(listed()).toEqual(["a records.pdf", "c records.pdf", "b records.pdf"]);

    await user.click(screen.getByRole("button", { name: "Move a records.pdf down" }));
    expect(listed()).toEqual(["c records.pdf", "a records.pdf", "b records.pdf"]);

    await user.click(screen.getByRole("button", { name: "Remove a records.pdf" }));
    expect(listed()).toEqual(["c records.pdf", "b records.pdf"]);
    // The removed file is dropped on the server too.
    expect(unstage).toHaveBeenCalledWith("tok-a records.pdf");
  });
});

describe("ReceivedFilesDialog reading the files", () => {
  it("sends each file on its own as it is added, and shows its pages and the total", async () => {
    readings = { "a.pdf": { pages: 1 }, "b.pdf": { pages: 12 } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a.pdf"), pdf("b.pdf")]);

    expect(screen.getAllByText("Reading...")).toHaveLength(2);
    await allRead(2);

    expect(stage.mock.calls.map(([file]) => (file as File).name)).toEqual(["a.pdf", "b.pdf"]);
    expect(screen.getByText("1 page")).toBeInTheDocument();
    expect(screen.getByText("12 pages")).toBeInTheDocument();
    expect(screen.getByText(/^13 pages in all/)).toBeInTheDocument();
  });

  it("shows why a file was refused, and cannot send until it is removed", async () => {
    const user = userEvent.setup();
    stage.mockImplementationOnce(async () => {
      throw new ApiError("This file is not a readable PDF.", 400);
    });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("broken.pdf"), pdf("records.pdf")]);
    await fillPatient(user);
    await waitFor(() => expect(stage).toHaveBeenCalledTimes(2));

    expect(await screen.findByText("This file is not a readable PDF.")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Combine & upload" });
    expect(send).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Remove broken.pdf" }));
    await confirmAll(user);
    await waitFor(() => expect(send).toBeEnabled());
    expect(unstage).not.toHaveBeenCalled();
  });

  it("says plainly when a file is too large for the server", async () => {
    stage.mockImplementationOnce(async () => {
      throw new ApiError("Request Entity Too Large", 413);
    });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("huge.pdf")]);

    expect(
      await screen.findByText("This file is larger than the server accepts in one upload."),
    ).toBeInTheDocument();
  });

  it("sends three files at a time, the next as soon as one is read", async () => {
    // One at a time, each file waiting for its first page to be read, ten files took minutes.
    const waits = [deferredStage(), deferredStage(), deferredStage(), deferredStage()];
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"), pdf("d.pdf"), pdf("e.pdf")]);

    await waitFor(() => expect(stage).toHaveBeenCalledTimes(3));
    const sent = () => stage.mock.calls.map(([file]) => (file as File).name);
    expect(sent()).toEqual(["a.pdf", "b.pdf", "c.pdf"]);

    await act(async () => waits[1].answer({ token: "tb", pages: 2, kind: "records" }));
    await waitFor(() => expect(stage).toHaveBeenCalledTimes(4));
    expect(sent()[3]).toBe("d.pdf");

    await act(async () => {
      waits[0].answer({ token: "ta", pages: 2, kind: "records" });
      waits[2].answer({ token: "tc", pages: 2, kind: "records" });
      waits[3].answer({ token: "td", pages: 2, kind: "records" });
    });
    await allRead(5);
    expect(sent()).toEqual(["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"]);
  });

  it("never sends a file removed before its turn", async () => {
    const user = userEvent.setup();
    const waits = [deferredStage(), deferredStage(), deferredStage()];
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"), pdf("d.pdf")]);
    await waitFor(() => expect(stage).toHaveBeenCalledTimes(3));

    await user.click(screen.getByRole("button", { name: "Remove d.pdf" }));
    await act(async () => waits[0].answer({ token: "ta", pages: 2, kind: "records" }));

    expect(stage).toHaveBeenCalledTimes(3);
    await act(async () => {
      waits[1].answer({ token: "tb", pages: 2, kind: "records" });
      waits[2].answer({ token: "tc", pages: 2, kind: "records" });
    });
    expect(stage).toHaveBeenCalledTimes(3);
  });

  it("drops on the server a file removed while it was still being read", async () => {
    const user = userEvent.setup();
    const slow = deferredStage();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("slow.pdf")]);

    await user.click(screen.getByRole("button", { name: "Remove slow.pdf" }));
    await act(async () => slow.answer({ token: "late", pages: 2, kind: "records" }));

    expect(unstage).toHaveBeenCalledWith("late");
    expect(noFiles()).toBe(true);
  });
});

describe("ReceivedFilesDialog sending", () => {
  it("sends the files in the order listed, with their kinds and the patient's details", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 6, patient_records: 0 });
    readings = { "scan.pdf": { kind: "advocacy_letter" } };
    render(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    addFiles([pdf("records.pdf"), pdf("scan.pdf")]);
    await allRead(2);
    await fillPatient(user);
    await confirmAll(user);

    await user.click(screen.getByRole("button", { name: "Combine & upload" }));

    await waitFor(() => expect(combine.mutateAsync).toHaveBeenCalledTimes(1));
    expect(combine.mutateAsync).toHaveBeenCalledWith({
      lastName: "Synthetic",
      firstName: "Patient",
      patientId: "801234",
      dateReceived: "2026-11-03",
      recordsFrom: "applicant",
      billing: {
        visit_type: "QME",
        appointment_date: "2026-12-01",
        doctor: "",
        interpreter: "no",
        claim_numbers: "",
        location: "",
        previous_visit: "",
        declared_pages: "",
      },
      files: [
        { token: "tok-scan.pdf", kind: "advocacy_letter" },
        { token: "tok-records.pdf", kind: "records" },
      ],
      owner: null,
    });
    expect(vi.mocked(toast).success).toHaveBeenCalledWith(
      "One record made from 2 files, 6 pages.",
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    // The server used the files and deleted them itself.
    expect(unstage).not.toHaveBeenCalled();
  });

  it("says the record went to the team queue when the server put it there", async () => {
    // The office's answers: a record belongs to nobody until someone starts working on it
    // (2026-11-25), and a record that came as one file is uploaded as it is (2026-11-26).
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({
      id: "d1",
      page_count: 1,
      patient_records: 0,
      in_team_queue: true,
    });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(vi.mocked(toast).success).toHaveBeenCalledWith(
        "Record uploaded, 1 page, in the team queue.",
      ),
    );
  });

  it("reads the patient ID from the files' names when they agree", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    readings = { "Synthetic 801234 email.pdf": { kind: "email" } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("Synthetic 801234 email.pdf"), pdf("Synthetic-801234-records.pdf")]);
    await allRead(2);
    await fillPatient(user, { id: false });

    expect(screen.getByLabelText("Patient ID")).toHaveValue("801234");
    await confirmAll(user);
    await user.click(screen.getByRole("button", { name: "Combine & upload" }));

    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ patientId: "801234" }),
      ),
    );
  });

  it("asks for every detail, and every file read, before it can send", async () => {
    const user = userEvent.setup();
    const slow = deferredStage();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    const send = () => screen.getByRole("button", { name: "Upload" });
    expect(send()).toBeDisabled();

    addFiles([pdf("records.pdf")]);
    const set = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    set("Patient last name", "Synthetic");
    set("Patient first name", "Patient");
    set("Patient ID", "801234");
    set("Date received", "2026-11-03");
    expect(send()).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Records from"), "copy_service");
    expect(send()).toBeDisabled();
    // The billing sheet's required fields, one at a time.
    await user.selectOptions(screen.getByLabelText("Type of visit"), "QME");
    expect(send()).toBeDisabled();
    set("Appointment date", "2026-12-01");
    expect(send()).toBeDisabled();
    // Who reviews the records is not asked (2026-11-20): the sheet always says IN-HOUSE.
    expect(screen.queryByLabelText("Reviewed by")).toBeNull();
    await user.selectOptions(screen.getByLabelText("Interpreter"), "yes");
    // Still reading the file.
    expect(send()).toBeDisabled();
    await act(async () =>
      slow.answer({ token: "t1", pages: 2, kind: "records", check: false, sha256: "s1" }),
    );
    // Read. A file alone is the record as it came: there is no kind to confirm.
    expect(send()).toBeEnabled();

    set("Patient ID", "x".repeat(40));
    expect(send()).toBeDisabled();
    expect(screen.getByText(/at most 32 letters and digits/)).toBeInTheDocument();
    set("Patient ID", "801234");
    expect(send()).toBeEnabled();

    // A declared page count that is not a whole number holds the upload back, and says so.
    set("Pages on the declaration", "six");
    expect(send()).toBeDisabled();
    expect(screen.getByText("Enter the page count as a whole number.")).toBeInTheDocument();
    set("Pages on the declaration", "6");
    expect(send()).toBeEnabled();
  });

  it.each([
    ["a supplemental report", "SUPP"],
    ["late meds", "LATE"],
  ])("asks no appointment date for %s", async (_name, visit) => {
    // The office's answers: a supplemental report (2026-11-19) and late meds (2026-11-25) have no
    // appointment, so the box goes away and the record can be made without one.
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);
    fireEvent.change(screen.getByLabelText("Appointment date"), { target: { value: "" } });
    const send = screen.getByRole("button", { name: "Upload" });
    expect(send).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Type of visit"), visit);

    expect(screen.queryByLabelText("Appointment date")).toBeNull();
    expect(send).toBeEnabled();
    await user.click(send);
    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          billing: expect.objectContaining({ visit_type: visit }),
        }),
      ),
    );
  });

  it("offers the doctors and sends the optional billing fields typed", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);
    await confirmAll(user);

    await user.selectOptions(screen.getByLabelText("Doctor"), "Beta");
    fireEvent.change(screen.getByLabelText("Claim numbers"), { target: { value: "ADJ1, ADJ2" } });
    fireEvent.change(screen.getByLabelText("Location"), { target: { value: "Main office" } });
    fireEvent.change(screen.getByLabelText("Previous office visit"), {
      target: { value: "2026-06-14" },
    });
    fireEvent.change(screen.getByLabelText("Pages on the declaration"), {
      target: { value: "40" },
    });
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          billing: expect.objectContaining({
            doctor: "Beta",
            claim_numbers: "ADJ1, ADJ2",
            location: "Main office",
            previous_visit: "2026-06-14",
            declared_pages: "40",
          }),
        }),
      ),
    );
  });

  it("names the reviewer and sends the record to them when an admin uploads for one", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 1, patient_records: 2 });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} owner={{ id: 7, name: "Brian" }} />);
    expect(screen.getByRole("heading", { name: "Upload records for Brian" })).toBeInTheDocument();
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);

    await user.click(screen.getByRole("button", { name: "Upload for Brian" }));

    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ owner: 7 })),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith(
      expect.stringMatching(/^Record uploaded, 1 page, in Brian's records\. /),
    );
  });

  it("toasts a humanized message when the upload fails, and keeps what was added", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);

    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
    expect(listed()).toEqual(["records.pdf"]);
    expect(unstage).not.toHaveBeenCalled();
  });

  it("shows that it is uploading while the upload runs", () => {
    combine.isPending = true;
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Uploading..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("shows that it is combining several files while the upload runs", async () => {
    combine.isPending = true;
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("letter.pdf"), pdf("records.pdf")]);
    await allRead(2);
    expect(screen.getByRole("button", { name: "Combining..." })).toBeDisabled();
  });
});

describe("ReceivedFilesDialog file handling", () => {
  it("says so when a picked file is not a PDF, and keeps the PDFs", async () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("a.pdf"), other("b.doc"), other("c.doc"), pdf("d.pdf")]);

    expect(vi.mocked(toast).error).toHaveBeenCalledWith(
      "Only PDFs and scanned images (JPG, PNG, TIFF) can be added; save Word or Outlook files " +
        "as PDF first. 2 files were not added.",
    );
    expect(listed()).toEqual(["a.pdf", "d.pdf"]);
    await allRead(2);
  });

  it("says one file when one is not a PDF", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([other("b.doc")]);
    expect(vi.mocked(toast).error).toHaveBeenCalledWith(
      "Only PDFs and scanned images (JPG, PNG, TIFF) can be added; save Word or Outlook files " +
        "as PDF first. One file was not added.",
    );
    expect(stage).not.toHaveBeenCalled();
  });

  it("clears everything on Cancel and drops the files on the server", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const { rerender } = render(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    addFiles([pdf("a.pdf"), pdf("b.pdf")]);
    await allRead(2);
    await user.type(screen.getByLabelText("Patient last name"), "Synthetic");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(unstage.mock.calls.map(([token]) => token).sort()).toEqual(["tok-a.pdf", "tok-b.pdf"]);
    rerender(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    expect(noFiles()).toBe(true);
    expect(screen.getByLabelText("Patient last name")).toHaveValue("");
  });

  it("clears everything when dismissed with Escape too", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const { rerender } = render(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    addFiles([pdf("a.pdf")]);
    await allRead(1);

    await user.keyboard("{Escape}");

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(unstage).toHaveBeenCalledWith("tok-a.pdf");
    rerender(<ReceivedFilesDialog open onOpenChange={onOpenChange} />);
    expect(noFiles()).toBe(true);
  });
});


describe("ReceivedFilesDialog checking", () => {
  it("needs every file confirmed by the uploader, even a sure reading", async () => {
    // DEMONSTRATES: the model pre-fills the kind; a person confirms each one against its page.
    const user = userEvent.setup();
    readings = { "letter.pdf": { kind: "advocacy_letter" } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("letter.pdf"), pdf("records.pdf")]);
    await allRead(2);
    await fillPatient(user);
    const send = screen.getByRole("button", { name: "Combine & upload" });

    expect(screen.queryByText("Not sure - check the page")).toBeNull();
    expect(
      screen.getByText(/2 files to confirm before the record can be made/),
    ).toBeInTheDocument();
    expect(send).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Confirm letter.pdf as Advocacy letter" }));
    expect(send).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Confirm records.pdf as Medical records" }),
    );

    expect(send).toBeEnabled();
    expect(screen.queryByText(/to confirm/)).toBeNull();
  });

  it("asks the uploader to check an unsure reading before the record can be made", async () => {
    const user = userEvent.setup();
    readings = { "scan.pdf": { kind: "declaration", check: true } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("scan.pdf"), pdf("records.pdf")]);
    await allRead(2);
    await fillPatient(user);
    await user.click(
      screen.getByRole("button", { name: "Confirm records.pdf as Medical records" }),
    );

    expect(screen.getByText("Not sure - check the page")).toBeInTheDocument();
    expect(screen.getByText(/1 file to confirm before the record can be made/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Combine & upload" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Confirm scan.pdf as Declaration" }));

    expect(screen.queryByText("Not sure - check the page")).toBeNull();
    expect(isConfirmed("scan.pdf", "2 pages")).toBe(true);
    expect(screen.getByRole("button", { name: "Combine & upload" })).toBeEnabled();
  });

  it("asks for a look when the name says one kind and a sure reading another", async () => {
    // A copy service's packet named "POS.pdf" whose first page is a subpoena's proof of service:
    // the model reads records; the name is not trusted over it, but it is worth a look.
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("POS.pdf"), pdf("records.pdf")]);
    await allRead(2);

    expect(screen.getByLabelText("What POS.pdf is")).toHaveValue("records");
    expect(screen.getByText("Not sure - check the page")).toBeInTheDocument();
  });

  it("choosing a kind for a flagged file answers the check too", async () => {
    const user = userEvent.setup();
    readings = { "scan.pdf": { check: true } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("scan.pdf"), pdf("records.pdf")]);
    await allRead(2);

    await user.selectOptions(screen.getByLabelText("What scan.pdf is"), "medical_index");

    expect(screen.queryByText("Not sure - check the page")).toBeNull();
    expect(isConfirmed("scan.pdf", "2 pages")).toBe(true);
  });

  it("names a file added twice and cannot send until one is removed", async () => {
    const user = userEvent.setup();
    readings = { "copy.pdf": { sha256: "same" }, "original.pdf": { sha256: "same" } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("original.pdf"), pdf("copy.pdf")]);
    await allRead(2);
    await fillPatient(user);

    expect(screen.getByText("Same file as original.pdf. Remove one of them.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Combine & upload" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Remove copy.pdf" }));

    // One file left: the record as it came, nothing to confirm.
    expect(screen.queryByText(/Same file as/)).toBeNull();
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("sends a refused file again on Try again", async () => {
    const user = userEvent.setup();
    stage.mockImplementationOnce(async () => {
      throw new ApiError("network", 0);
    });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await waitFor(() => expect(stage).toHaveBeenCalledTimes(1));
    await screen.findByRole("button", { name: "Try records.pdf again" });

    await user.click(screen.getByRole("button", { name: "Try records.pdf again" }));

    await allRead(2);
    expect(screen.getByText("2 pages")).toBeInTheDocument();
  });

  it("shows a file's first page on request", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    expect(screen.queryByRole("img", { name: "First page of records.pdf" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show the first page of records.pdf" }));

    expect(screen.getByRole("img", { name: "First page of records.pdf" })).toHaveAttribute(
      "src",
      "/api/documents/combine/files/tok-records.pdf/preview",
    );
  });

  it("takes a scanned image as one of the files", async () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([new File([new Uint8Array([1])], "fax page.tif", { type: "image/tiff" })]);
    await allRead(1);
    expect(listed()).toEqual(["fax page.tif"]);
    expect(vi.mocked(toast).error).not.toHaveBeenCalled();
  });
});

describe("ReceivedFilesDialog drag and drop", () => {
  it("adds files dropped anywhere on it, and the page under it never gets the drop", async () => {
    // DEMONSTRATES the office's first use (2026-11-20): files dropped on the dialog were taken by
    // the records page it was opened from, which uploaded the first one alone as a record.
    const pageDrop = vi.fn();
    const pageOver = vi.fn();
    render(
      <div onDrop={pageDrop} onDragOver={pageOver}>
        <ReceivedFilesDialog open onOpenChange={vi.fn()} />
      </div>,
    );
    const dialog = screen.getByRole("dialog");

    expect(fireEvent.dragOver(dialog)).toBe(false);
    expect(
      fireEvent.drop(screen.getByLabelText("Patient last name"), {
        dataTransfer: { files: [pdf("letter.pdf"), pdf("records.pdf")] },
      }),
    ).toBe(false);

    expect(listed()).toEqual(["letter.pdf", "records.pdf"]);
    expect(pageDrop).not.toHaveBeenCalled();
    expect(pageOver).not.toHaveBeenCalled();
    await allRead(2);
  });

  it("says files can be dropped, and lights up while they are dragged over it", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    const dialog = screen.getByRole("dialog");

    expect(screen.getByText("Drag the file or files here, in any order, or")).toBeInTheDocument();
    fireEvent.dragEnter(dialog);
    expect(screen.getByText("Drop the files to add them")).toBeInTheDocument();
    fireEvent.dragLeave(dialog);
    expect(screen.queryByText("Drop the files to add them")).toBeNull();
  });

  it("stays lit while files are dragged from one of its parts to another", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    const dialog = screen.getByRole("dialog");

    fireEvent.dragEnter(dialog);
    fireEvent.dragEnter(screen.getByLabelText("Patient ID"));
    fireEvent.dragLeave(dialog);

    expect(screen.getByText("Drop the files to add them")).toBeInTheDocument();
  });

  it("refuses a dropped file it cannot take, as it does a picked one", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);

    fireEvent.drop(screen.getByRole("dialog"), { dataTransfer: { files: [other("note.doc")] } });

    expect(vi.mocked(toast).error).toHaveBeenCalledWith(expect.stringContaining("not added"));
    expect(stage).not.toHaveBeenCalled();
  });

  it("takes no files while the record is being made", () => {
    combine.isPending = true;
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);

    fireEvent.drop(screen.getByRole("dialog"), { dataTransfer: { files: [pdf("late.pdf")] } });

    expect(stage).not.toHaveBeenCalled();
    expect(noFiles()).toBe(true);
  });
});

describe("ReceivedFilesDialog layout", () => {
  it("keeps its buttons outside the part that scrolls, so zooming in never hides them", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    const body = screen.getByLabelText("Patient last name").closest(".overflow-y-auto");

    expect(body).not.toBeNull();
    expect(body).not.toContainElement(screen.getByRole("button", { name: "Upload" }));
    expect(body).not.toContainElement(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog")).toHaveClass("max-h-[calc(100dvh-2rem)]");
  });

  it("marks a confirmed file, and a file it is not sure of", async () => {
    const user = userEvent.setup();
    readings = { "scan.pdf": { check: true } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf"), pdf("scan.pdf")]);
    await allRead(2);

    expect(rowOf("scan.pdf")).toHaveClass("border-warning/60");
    expect(rowOf("records.pdf")).not.toHaveClass("border-success/40");
    await user.click(
      screen.getByRole("button", { name: "Confirm records.pdf as Medical records" }),
    );

    expect(rowOf("records.pdf")).toHaveClass("border-success/40");
    expect(isConfirmed("records.pdf", "2 pages")).toBe(true);
  });
});

describe("ReceivedFilesDialog one file (the office's answer, 2026-11-26)", () => {
  it("uploads one file as it is: no kind asked, sent as the medical records", async () => {
    // DEMONSTRATES the request: one button for every upload. A record that came as one file has
    // nothing to put together, but the patient, the dates and the billing sheet are still given.
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    readings = { "letter first.pdf": { kind: "advocacy_letter" } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Upload records" })).toBeInTheDocument();
    addFiles([pdf("letter first.pdf")]);
    await allRead(1);

    expect(screen.queryByLabelText("What letter first.pdf is")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Confirm / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Move / })).toBeNull();
    expect(screen.queryByText(/to confirm/)).toBeNull();
    expect(within(rowOf("letter first.pdf")).getByText("2 pages")).toBeInTheDocument();
    await fillPatient(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    // Whatever its first page reads as, the whole file is the records: every page is counted.
    await waitFor(() =>
      expect(combine.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ files: [{ token: "tok-letter first.pdf", kind: "records" }] }),
      ),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith("Record uploaded, 2 pages.");
  });

  it("asks every file's kind once a second file is added", async () => {
    const user = userEvent.setup();
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();

    addFiles([pdf("letter.pdf")]);
    await allRead(2);

    expect(screen.getByLabelText("What records.pdf is")).toHaveValue("records");
    expect(screen.getByText(/2 files to confirm before the record/)).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Combine & upload" });
    expect(send).toBeDisabled();
    await confirmAll(user);
    expect(send).toBeEnabled();
  });
});

describe("ReceivedFilesDialog files uploaded before", () => {
  // A record the office already has the file in: Brian's, uploaded at noon UTC (the same day in
  // every US time zone).
  const BRIANS = {
    uploaded_at: "2026-11-24T12:00:00+00:00",
    pages: 1271,
    in_team_queue: false,
    yours: false,
    reviewer: "Brian",
    part_of_record: false,
  };
  const WARNING = "Already uploaded on 11/24/2026, in Brian's records (1,271 pages).";

  it("warns on a file the office already has, and sends it once the uploader says so", async () => {
    // DEMONSTRATES the request: an identical file uploaded before is warned about - on its card
    // at once, and again before anything is sent - and the uploader may still go on.
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    readings = { "records.pdf": { before: [BRIANS] } };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);

    expect(within(rowOf("records.pdf")).getByText(WARNING)).toBeInTheDocument();
    expect(rowOf("records.pdf")).toHaveClass("border-warning/60");
    await fillPatient(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    const question = screen.getByRole("alert");
    expect(question).toHaveTextContent("This file was uploaded before. Upload anyway?");
    expect(question).toHaveTextContent(`records.pdf: ${WARNING}`);
    expect(combine.mutateAsync).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Go back" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(combine.mutateAsync).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Upload" }));
    await user.click(screen.getByRole("button", { name: "Upload anyway" }));
    await waitFor(() => expect(combine.mutateAsync).toHaveBeenCalledTimes(1));
  });

  it("names every such file among several, and asks nothing once they are removed", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 4, patient_records: 0 });
    readings = {
      "letter.pdf": { kind: "advocacy_letter", before: [{ ...BRIANS, part_of_record: true }] },
      "records.pdf": { before: [{ ...BRIANS, in_team_queue: true }] },
    };
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("letter.pdf"), pdf("records.pdf"), pdf("email.pdf")]);
    await allRead(3);
    await fillPatient(user);
    await confirmAll(user);
    await user.click(screen.getByRole("button", { name: "Combine & upload" }));

    const question = screen.getByRole("alert");
    expect(question).toHaveTextContent("These 2 files were uploaded before. Upload anyway?");
    expect(question).toHaveTextContent(
      "letter.pdf: Already uploaded on 11/24/2026 as part of a record, in Brian's records",
    );
    expect(question).toHaveTextContent("records.pdf: Already uploaded on 11/24/2026, in the team");

    await user.click(screen.getByRole("button", { name: "Remove letter.pdf" }));
    await user.click(screen.getByRole("button", { name: "Remove records.pdf" }));

    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(combine.mutateAsync).toHaveBeenCalledTimes(1));
  });

  it("asks nothing for files the office does not have", async () => {
    const user = userEvent.setup();
    combine.mutateAsync.mockResolvedValue({ id: "d1", page_count: 2, patient_records: 0 });
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} />);
    addFiles([pdf("records.pdf")]);
    await allRead(1);
    await fillPatient(user);

    expect(screen.queryByText(/Already uploaded/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(combine.mutateAsync).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("ReceivedFilesDialog files dropped on the records page", () => {
  it("adds them once, as if picked - even where React runs its effects twice", async () => {
    const dropped = [pdf("letter.pdf"), pdf("records.pdf")];
    const { rerender } = render(
      <StrictMode>
        <ReceivedFilesDialog open onOpenChange={vi.fn()} dropped={dropped} />
      </StrictMode>,
    );

    await allRead(2);
    expect(listed()).toEqual(["letter.pdf", "records.pdf"]);

    rerender(
      <StrictMode>
        <ReceivedFilesDialog open onOpenChange={vi.fn()} dropped={dropped} />
      </StrictMode>,
    );
    expect(stage).toHaveBeenCalledTimes(2);

    // A new drop is a new list, added to what is there.
    rerender(
      <StrictMode>
        <ReceivedFilesDialog open onOpenChange={vi.fn()} dropped={[pdf("email.pdf")]} />
      </StrictMode>,
    );
    await allRead(3);
    expect(listed()).toHaveLength(3);
  });

  it("refuses a dropped file it cannot take, as it does a picked one", () => {
    render(<ReceivedFilesDialog open onOpenChange={vi.fn()} dropped={[other("note.doc")]} />);

    expect(vi.mocked(toast).error).toHaveBeenCalledWith(expect.stringContaining("not added"));
    expect(stage).not.toHaveBeenCalled();
  });

  it("adds nothing while it is closed", () => {
    render(<ReceivedFilesDialog open={false} onOpenChange={vi.fn()} dropped={[pdf("a.pdf")]} />);
    expect(stage).not.toHaveBeenCalled();
  });
});
