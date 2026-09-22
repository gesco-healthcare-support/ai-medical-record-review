/**
 * The bundle builder's journey: picking a record, switching between records, filling the header,
 * and moving between the two bundles.
 *
 * A SIBLING of bundle-page-client.test.tsx rather than more of it. That file's fixtures are module
 * constants - one record, a navigation spy rebuilt on every call, no reset between tests - and its
 * eleven tests stand on them. Everything here needs the opposite: several records, a spy that can
 * be observed, and state that starts clean every time.
 *
 * THE TEST WORTH READING IS "never carries the last patient's header into the next record".
 * Prefill fills a header field only while it is EMPTY (`setPatient((p) => p || full)`), so the
 * resets in `chooseAnother` are the only thing between record A's patient and record B's export.
 * It opens two records with DIFFERENT headers on purpose: clearing the fields of one record would
 * pass with every reset deleted, because an empty field is also what a never-filled one looks like.
 *
 * Names, dates of birth and firms are obviously synthetic tokens, never realistic values.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentDetail, DocumentListItem, Row } from "@/lib/types";
import type { HeaderFields } from "@/lib/review-api";

// Hoisted, and read through arrows so the hoisted mock factories touch them only at call time.
const push = vi.fn();
const getDocument = vi.fn();
const extractHeader = vi.fn();
const downloadBundlePdf = vi.fn();
const downloadBundleSummary = vi.fn();
const docs: { list: DocumentListItem[] } = { list: [] };

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/hooks/use-documents", () => ({ useDocuments: () => ({ data: docs.list }) }));
vi.mock("@/lib/review-api", () => ({
  getDocument: (...args: unknown[]) => getDocument(...args),
  extractHeader: (...args: unknown[]) => extractHeader(...args),
}));
// The REAL configs are kept - the tabs test needs the real slug to know which tab is open - and
// only the two network calls are replaced.
vi.mock("@/lib/bundle-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/bundle-api")>()),
  downloadBundlePdf: (...args: unknown[]) => downloadBundlePdf(...args),
  downloadBundleSummary: (...args: unknown[]) => downloadBundleSummary(...args),
}));

import { DIAGNOSTIC_OPERATIVE } from "@/lib/bundle-api";
import { BundlePageClient } from "@/components/bundle/bundle-page-client";

function listItem(id: string, overrides: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
    id,
    original_filename: `${id}.pdf`,
    page_count: 5,
    status: "reviewing",
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    active_job: null,
    rows_count: 1,
    patient_first_name: "",
    patient_last_name: "",
    patient_name: "",
    patient_dob: "",
    law_firm: "",
    ...overrides,
  };
}

/** A row in category "3", which DIAGNOSTIC_OPERATIVE bundles - so the download buttons enable. */
const MATCHED: Row = {
  start: 1,
  end: 3,
  category: "3",
  title: "MRI",
  date: "",
  injury_date: "",
  flag: "-",
  suggest_merge: false,
  include: true,
};

function detail(id: string, overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id,
    original_filename: `${id}.pdf`,
    page_count: 5,
    status: "reviewing",
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    active_job: null,
    patient_first_name: "",
    patient_last_name: "",
    patient_name: "",
    patient_dob: "",
    law_firm: "",
    rows: [MATCHED],
    categories: [{ id: "3", name: "Imaging" }],
    ...overrides,
  };
}

/** Two records whose persisted headers DIFFER in every field the switching test reads. */
const DETAILS: Record<string, DocumentDetail> = {
  "rec-a": detail("rec-a", {
    original_filename: "alpha-record.pdf",
    patient_first_name: "SYNTH",
    patient_last_name: "ALPHA",
    patient_name: "SYNTH ALPHA",
    patient_dob: "DOB-ALPHA",
    law_firm: "Firm Alpha LLP",
  }),
  "rec-b": detail("rec-b", {
    original_filename: "bravo-record.pdf",
    patient_first_name: "SYNTH",
    patient_last_name: "BRAVO",
    patient_name: "SYNTH BRAVO",
    patient_dob: "DOB-BRAVO",
    law_firm: "Firm Bravo LLP",
  }),
};

/** A record with NO persisted header, so every value in the fields came from what the test did. */
const BARE = detail("rec-a", { original_filename: "alpha-record.pdf" });

beforeEach(() => {
  push.mockReset();
  getDocument.mockReset().mockImplementation((id: string) => Promise.resolve(DETAILS[id]));
  extractHeader.mockReset();
  downloadBundlePdf.mockReset();
  downloadBundleSummary.mockReset().mockResolvedValue(undefined);
  docs.list = [
    listItem("rec-a", { original_filename: "alpha-record.pdf" }),
    listItem("rec-b", { original_filename: "bravo-record.pdf" }),
  ];
});

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <BundlePageClient config={DIAGNOSTIC_OPERATIVE} />
    </QueryClientProvider>,
  );
  return { user };
}

/** The picker's row for a record, found from its visible name. */
function pickerRow(name: string) {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

/**
 * Opens a record from the picker and waits for its BUILD PANEL, not for the breadcrumb. The
 * breadcrumb appears the moment a record is chosen, while its detail is still loading - a barrier
 * on it matches before the data arrives, which is the race that made a test flake in #359.
 */
async function openRecord(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(within(pickerRow(name)).getByRole("button", { name: "Select" }));
  return screen.findByLabelText("Patient name");
}

/** The picker is showing - its heading is the one thing it has that an open record does not. */
function pickerHeading() {
  return screen.findByRole("heading", { name: `${DIAGNOSTIC_OPERATIVE.label} builder` });
}

describe("picking a record", () => {
  it("lists the reviewer's records newest first", () => {
    // Built so that NO other ordering a comparator could plausibly produce matches the right one.
    // Input order is X, Y, Z; the correct order is Y, Z, X.
    //
    //   field               X            Y             Z            order it would give
    //   created_at desc     2026-01-01   2026-03-01    2026-02-01   Y, Z, X   <- correct
    //   created_at asc                                              X, Z, Y
    //   no sort at all                                              X, Y, Z
    //   updated_at d / a    2026-04-02   2026-04-01    2026-04-03   Z, X, Y / Y, X, Z
    //   page_count d / a    30           20            10           X, Y, Z / Z, Y, X
    //   filename a / d      bravo-...    charlie-...   alpha-...    Z, X, Y / Y, X, Z
    //   id a / d            d2           d1            d3           Y, X, Z / Z, X, Y
    //
    // A fixture that only distinguishes created_at from input order would pass with the
    // comparator reading any of the other fields - the #355 sort-fixture gap.
    docs.list = [
      listItem("d2", {
        original_filename: "bravo-record.pdf",
        created_at: "2026-01-01",
        updated_at: "2026-04-02",
        page_count: 30,
      }),
      listItem("d1", {
        original_filename: "charlie-record.pdf",
        created_at: "2026-03-01",
        updated_at: "2026-04-01",
        page_count: 20,
      }),
      listItem("d3", {
        original_filename: "alpha-record.pdf",
        created_at: "2026-02-01",
        updated_at: "2026-04-03",
        page_count: 10,
      }),
    ];
    renderBuilder();

    const names = screen
      .getAllByRole("row")
      .slice(1) // the header row
      .map((row) => within(row).getByText(/-record\.pdf$/).textContent);

    expect(names).toEqual(["charlie-record.pdf", "alpha-record.pdf", "bravo-record.pdf"]);
  });

  it("opens a record when its name is clicked, not only from Select", async () => {
    // The name, not the Select button. That button's cell calls stopPropagation, so a click on it
    // never reaches the row - which is why the row's own handler had never run in any test.
    const { user } = renderBuilder();

    await user.click(screen.getByText("alpha-record.pdf"));

    expect(await screen.findByLabelText("Patient name")).toBeInTheDocument();
  });

  it("tells a reviewer with no records to upload one first", () => {
    docs.list = [];
    renderBuilder();

    expect(
      screen.getByText("No records yet. Upload one from My documents first."),
    ).toBeInTheDocument();
  });
});

describe("switching records", () => {
  it("never carries the last patient's header into the next record", async () => {
    const { user } = renderBuilder();
    const patient = await openRecord(user, "alpha-record.pdf");
    await waitFor(() => expect(patient).toHaveValue("SYNTH ALPHA"));

    // Everything chooseAnother resets is made non-default on record A first. Prefill has already
    // filled name, DOB and firm; the QME type is edited; a download leaves a result message.
    // fireEvent is safe for the QME field - it has no `disabled` and no per-keystroke behaviour.
    const qme = screen.getByLabelText(/Evaluation type/) as HTMLInputElement;
    const defaultQme = qme.value;
    fireEvent.change(qme, { target: { value: "AME" } });
    await user.click(screen.getByRole("button", { name: /Download combined PDF/i }));
    expect(await screen.findByText("Combined PDF downloaded.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Choose another record" }));
    // waitFor around a query, not `await findByRole`: a findBy that times out throws its OWN error
    // before this message is ever read, and the probe for this line needs to see the message.
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: `${DIAGNOSTIC_OPERATIVE.label} builder` }),
        "choosing another record did not return to the picker",
      ).not.toBeNull(),
    );

    const next = await openRecord(user, "bravo-record.pdf");
    // Messages on every assertion: six mutations fail THIS test, and each must be seen to fail it
    // at its own line rather than somewhere downstream.
    await waitFor(() =>
      expect(next, "the last patient's name carried into the next record").toHaveValue(
        "SYNTH BRAVO",
      ),
    );
    expect(
      screen.getByLabelText("DOB"),
      "the last patient's DOB carried into the next record",
    ).toHaveValue("DOB-BRAVO");
    expect(
      screen.getByLabelText("Attorney law firm"),
      "the last record's law firm carried into the next record",
    ).toHaveValue("Firm Bravo LLP");
    expect(
      screen.getByLabelText(/Evaluation type/),
      "the edited QME type carried into the next record",
    ).toHaveValue(defaultQme);
    expect(
      screen.queryByText("Combined PDF downloaded."),
      "the last record's result message carried into the next record",
    ).toBeNull();
  });

  it("offers a way back from a record that failed to load", async () => {
    getDocument.mockRejectedValueOnce(new Error("gone"));
    const { user } = renderBuilder();
    await user.click(within(pickerRow("alpha-record.pdf")).getByRole("button", { name: "Select" }));

    const pane = (await screen.findByText("Could not load this record")).closest(
      ".bnd-empty",
    ) as HTMLElement;
    // The breadcrumb has a button with the same name. This pane's own one, specifically.
    await user.click(within(pane).getByRole("button", { name: "Choose another record" }));

    expect(await pickerHeading()).toBeInTheDocument();
  });

  it("offers a way back from a record that is not identified yet", async () => {
    getDocument.mockResolvedValueOnce({ ...BARE, rows: [] });
    const { user } = renderBuilder();
    await user.click(within(pickerRow("alpha-record.pdf")).getByRole("button", { name: "Select" }));

    const pane = (await screen.findByText(/been identified yet/)).closest(".bnd-empty") as HTMLElement;
    await user.click(within(pane).getByRole("button", { name: "Choose another record" }));

    expect(await pickerHeading()).toBeInTheDocument();
  });
});

describe("filling the header from the record", () => {
  it("fills the header from the record on request", async () => {
    getDocument.mockResolvedValueOnce(BARE);
    extractHeader.mockResolvedValueOnce({
      patient_first_name: "SYNTH",
      patient_last_name: "FILLED",
      patient_dob: "DOB-FILLED",
      law_firm: "Firm Filled LLP",
    });
    const { user } = renderBuilder();
    const patient = await openRecord(user, "alpha-record.pdf");

    await user.click(screen.getByRole("button", { name: "Auto-fill header from record" }));

    // waitFor around a query, as in the switching test - a findBy timeout would throw its own error
    // before this message is read.
    await waitFor(() =>
      expect(
        screen.queryByText("Header details filled from the record."),
        "no confirmation after filling the header",
      ).not.toBeNull(),
    );
    expect(extractHeader, "the header was read from a different record").toHaveBeenCalledWith(
      "rec-a",
    );
    expect(patient, "the name is not first and last name joined").toHaveValue("SYNTH FILLED");
    expect(screen.getByLabelText("DOB"), "the DOB was not filled").toHaveValue("DOB-FILLED");
    expect(screen.getByLabelText("Attorney law firm"), "the firm was not filled").toHaveValue(
      "Firm Filled LLP",
    );
  });

  it('leaves the header blank, never "undefined", when the record has none', async () => {
    getDocument.mockResolvedValueOnce(BARE);
    // Cast: HeaderFields says these are always strings. This test exists for when they are not -
    // an extractor that finds nothing and omits the fields rather than sending "".
    extractHeader.mockResolvedValueOnce({} as HeaderFields);
    const { user } = renderBuilder();
    const patient = await openRecord(user, "alpha-record.pdf");
    const defaultQme = (screen.getByLabelText(/Evaluation type/) as HTMLInputElement).value;

    await user.click(screen.getByRole("button", { name: "Auto-fill header from record" }));
    await screen.findByText("Header details filled from the record.");

    // Visible for the name: `${undefined} ${undefined}` is "undefined undefined".
    expect(patient, "the patient name read 'undefined'").toHaveValue("");

    // NOT visible for DOB and firm: setDob(undefined) leaves an empty field looking empty, because
    // React keeps the DOM value when an input goes uncontrolled. What reaches the Word export is
    // where "" and undefined differ, so that is where this is asserted.
    await user.click(screen.getByRole("button", { name: "Summarize to Word" }));
    await waitFor(() => expect(downloadBundleSummary).toHaveBeenCalled());
    const sent = downloadBundleSummary.mock.lastCall?.[2] as Record<string, unknown>;
    expect(sent.patientName, "the export was sent an undefined patient name").toBe("");
    expect(sent.patientdob, "the export was sent an undefined DOB").toBe("");
    expect(sent.lawfirm, "the export was sent an undefined law firm").toBe("");
    expect(sent.QMEorAME).toBe(defaultQme);
  });

  it("says so when the header cannot be read", async () => {
    getDocument.mockResolvedValueOnce(BARE);
    extractHeader.mockRejectedValueOnce(new Error("extractor unavailable"));
    const { user } = renderBuilder();
    await openRecord(user, "alpha-record.pdf");

    await user.click(screen.getByRole("button", { name: "Auto-fill header from record" }));

    expect(await screen.findByText("Could not read the header.")).toBeInTheDocument();
  });

  it("shows it is reading while the header is extracted", async () => {
    getDocument.mockResolvedValueOnce(BARE);
    let finish: (fields: HeaderFields) => void = () => undefined;
    extractHeader.mockReturnValueOnce(
      new Promise<HeaderFields>((resolve) => {
        finish = resolve;
      }),
    );
    const { user } = renderBuilder();
    await openRecord(user, "alpha-record.pdf");

    // user.click, not fireEvent: whether the button is DISABLED is what this test is about, and
    // fireEvent walks straight through a disabled attribute.
    await user.click(screen.getByRole("button", { name: "Auto-fill header from record" }));

    const busy = screen.queryByRole("button", { name: "Reading..." });
    expect(busy, "the button did not say it was reading").not.toBeNull();
    expect(busy, "the button stayed clickable while reading").toBeDisabled();

    await act(async () => finish({} as HeaderFields));
    expect(
      await screen.findByRole("button", { name: "Auto-fill header from record" }),
    ).toBeEnabled();
  });
});

describe("the matched documents", () => {
  it("labels an untitled document, an unknown category and a dated row", async () => {
    getDocument.mockResolvedValueOnce({
      ...BARE,
      // "8" is in the bundle's categories but deliberately NOT in the catalog below.
      categories: [{ id: "3", name: "Imaging" }],
      rows: [
        { ...MATCHED, start: 1, end: 2, title: "-", date: "" },
        { ...MATCHED, start: 3, end: 4, category: "8", title: "Op note", date: "2026-02-03" },
      ],
    });
    const { user } = renderBuilder();
    await openRecord(user, "alpha-record.pdf");
    await screen.findByText("2 matching documents");

    expect(
      screen.queryByText("(untitled document)"),
      "an untitled document had no label",
    ).not.toBeNull();
    const dated = screen.getByText("Op note").closest("tr") as HTMLElement;
    expect(
      within(dated).queryByText("8"),
      "a category missing from the catalog lost its id",
    ).not.toBeNull();
    expect(
      within(dated).queryByText("2026-02-03"),
      "a dated row did not show its date",
    ).not.toBeNull();
  });
});

describe("moving between bundles", () => {
  it("switches bundles from the tabs, and ignores a click on the open one", async () => {
    const { user } = renderBuilder();

    // The absence FIRST, the positive after it in the same test - so the spy is shown to be live
    // inside the very test that relies on its silence.
    await user.click(screen.getByRole("tab", { name: "Diagnostic & Operative" }));
    expect(push, "clicking the bundle already open navigated anyway").not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: "Depositions" }));
    expect(push, "the other bundle's tab did not go to its page").toHaveBeenCalledWith(
      "/depositions",
    );
  });
});
