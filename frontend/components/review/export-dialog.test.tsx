import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExportDialog } from "@/components/review/export-dialog";

describe("ExportDialog error handling", () => {
  afterEach(() => vi.restoreAllMocks());

  function openDialog() {
    render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={2}
        excludedCount={0}
      />,
    );
  }

  it("names a transport failure, rather than showing a generic fallback", async () => {
    // CHANGED EXPECTATION, deliberately. This asserted the generic "Export failed." for a rejected
    // fetch, because the dialog's own `fetch` threw a plain Error that `humanizeError` cannot
    // classify. Going through `downloadFile` makes it ApiError(status 0), which is the same shape
    // apiFetch produces - so the export now gets the specific copy every other request already got
    // for the same failure. The old expectation was the defect, not the contract.
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("boom"));
    openDialog();
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
  });

  it("shows the server's own reason for a refusal", async () => {
    // The dialog never read the error body - it threw `export failed (500)` on the status alone -
    // so a 422 naming the document it could not read, or a 503 naming an AI outage, reached the
    // reviewer as "Export failed." and nothing else.
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 422,
      headers: new Headers(),
      json: async () => ({ detail: "One document could not be read." }),
    } as unknown as Response);
    openDialog();
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(await screen.findByText("One document could not be read.")).toBeInTheDocument();
  });

  it("does not report success when the session has ended", async () => {
    // A 401 used to `return`, so the dialog closed as though the file had been written. It now
    // throws like every other client, and the reviewer is told before the redirect lands.
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 401,
      headers: new Headers(),
      json: async () => ({}),
    } as unknown as Response);
    render(
      <ExportDialog
        open
        onOpenChange={onOpenChange}
        documentId="d1"
        includedCount={2}
        excludedCount={0}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Export to Word" }));

    expect(await screen.findByText(/session has ended/i)).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

describe("ExportDialog page numbers", () => {
  afterEach(() => vi.restoreAllMocks());

  function mockFetch() {
    // Since #389 the export POST answers with where to fetch the file, and the page clicks a link to it.
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ filename: "x.docx", url: "/api/documents/d1/downloads/tok" }),
    } as unknown as Response);
    // jsdom does not navigate; a real click here would log "Not implemented: navigation".
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    return fetchSpy;
  }

  function body(fetchSpy: ReturnType<typeof mockFetch>) {
    return JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
  }

  function open() {
    render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={1}
        excludedCount={0}
      />,
    );
  }

  it("is unchecked by default, so the export asks for no page numbers", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    expect(screen.getByLabelText(/include page numbers/i)).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(body(fetchSpy).includePageNumbers).toBe(false);
  });

  it("resets to unchecked when the dialog is reopened", async () => {
    const user = userEvent.setup();
    mockFetch();
    const { rerender } = render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={1}
        excludedCount={0}
      />,
    );
    await user.click(screen.getByLabelText(/include page numbers/i));
    expect(screen.getByLabelText(/include page numbers/i)).toBeChecked();

    const props = { onOpenChange: vi.fn(), documentId: "d1", includedCount: 1, excludedCount: 0 };
    rerender(<ExportDialog open={false} {...props} />);
    rerender(<ExportDialog open {...props} />);
    expect(screen.getByLabelText(/include page numbers/i)).not.toBeChecked();
  });

  it("downloads the memo from the same header fields, asking for nothing extra", async () => {
    // The memo's doctor comes from the review page's dropdown and its signature from the
    // account, so this dialog has no memo-only inputs. A first version added a "Doctor (memo
    // only)" box and a "Documents received on" box here, which duplicated a field the record
    // already carries and let the memo disagree with the report downloaded beside it.
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    await user.click(screen.getByRole("button", { name: "Download memo" }));
    expect(fetchSpy.mock.calls[0][0]).toContain("/export/memo");
    expect(Object.keys(body(fetchSpy)).sort()).toEqual([
      "QMEorAME",
      "includePageNumbers",
      "lawfirm",
      "patientName",
      "patientdob",
    ]);
  });

  it("offers no memo-only fields", () => {
    // GUARD against re-adding them. The dialog is where they were, and the reviewers' answer -
    // the header "is not too important, we can ignore that for now" - is why they went.
    open();
    expect(screen.queryByLabelText(/doctor/i)).toBeNull();
    expect(screen.queryByLabelText(/documents received on/i)).toBeNull();
  });

  it("sends the flag on the linked PDF too once checked", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    await user.click(screen.getByLabelText(/include page numbers/i));
    await user.click(screen.getByRole("button", { name: "Export to linked PDF" }));
    expect(fetchSpy.mock.calls[0][0]).toContain("/export/pdf");
    expect(body(fetchSpy).includePageNumbers).toBe(true);
  });
  it("downloads every deliverable in one archive, with both bundles named", async () => {
    // The whole point of the third button: one hand-over instead of four separate clicks. The
    // bundle list comes from `lib/bundle-api`, so this also pins that the dialog does not carry
    // its own copy of the category taxonomy.
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    await user.click(screen.getByRole("button", { name: "Download all (.zip)" }));
    expect(fetchSpy.mock.calls[0][0]).toContain("/export/zip");
    // CHANGED EXPECTATION, deliberately, twice over:
    //
    // Each bundle carries the heading of its cover page. Asked which file they wanted for
    // Diagnostics, the reviewers answered "just a PDF with the documents together. Preferably
    // with a cover page that includes a list of reports" - and said nothing of the sort about
    // Depositions, so that one sends no heading and gets no cover page. The asymmetry is the
    // point and is why this asserts both.
    //
    // Each also carries `downloadName`, the reader-facing name the server prepends the patient
    // name to. The reviewers asked for the diagnostic download to be named "similar to how the
    // other files are named" - three of the four archive members already carried the patient
    // name and the bundle did not, so this pins that the dialog sends what the server needs to
    // fix that. BOTH bundles send one: a folder holding one named file and one unnamed is the
    // inconsistency being removed, not a smaller version of it.
    expect(body(fetchSpy).bundles).toEqual([
      {
        label: "diagnostic-operative",
        categories: ["3", "8"],
        coverHeading: "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS",
        downloadName: "List of Diagnostic and Operative Reports",
      },
      {
        label: "depositions",
        categories: ["9"],
        coverHeading: undefined,
        downloadName: "Depositions",
      },
    ]);
  });

  it("puts the header fields on the archive too, not just the single-file exports", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    await user.click(screen.getByLabelText(/include page numbers/i));
    await user.click(screen.getByRole("button", { name: "Download all (.zip)" }));
    expect(body(fetchSpy).includePageNumbers).toBe(true);
  });

  it("sends each header box under its own field", async () => {
    // Four free-text boxes writing into four differently-named request fields. A box wired to the
    // wrong one fires its handler, updates, and exports happily - and the reviewer finds out when
    // the delivered document carries the date of birth where the law firm should be.
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();

    // Short, DISTINCT values: each box only has to carry its own value into its own field, and
    // every keystroke re-renders the dialog - long strings are what timed this test out under load.
    await user.type(screen.getByLabelText("Patient name"), "Vasquez");
    await user.type(screen.getByLabelText("DOB"), "04/05/1980");
    // Evaluation type is the one box that ships prefilled (the panel-QME wording), so it is cleared
    // rather than typed into - appending would assert against a value the test did not choose.
    await user.clear(screen.getByLabelText(/Evaluation type/i));
    await user.type(screen.getByLabelText(/Evaluation type/i), "QME");
    await user.type(screen.getByLabelText(/Attorney law firm/i), "Acme");
    await user.click(screen.getByRole("button", { name: "Export to Word" }));

    const sent = body(fetchSpy);
    expect(sent.patientName).toBe("Vasquez");
    expect(sent.patientdob).toBe("04/05/1980");
    expect(sent.QMEorAME).toBe("QME");
    expect(sent.lawfirm).toBe("Acme");
  });

  // A per-keystroke trim would turn "a b" into "ab" - see the same tests in header-bar.test.tsx.
  // Evaluation type is the one box that ships prefilled, so it alone is cleared first.
  it.each<[string, boolean]>([
    ["Patient name", false],
    ["Evaluation type (QME / AME)", true],
    ["Attorney law firm", false],
  ])("keeps a space typed into %s", async (label, startsPrefilled) => {
    const user = userEvent.setup();
    open();
    const box = screen.getByLabelText(label);
    if (startsPrefilled) await user.clear(box);
    await user.type(box, "a b");
    expect(box).toHaveValue("a b");
  });

  it("closes without exporting when Cancel is used", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    const onOpenChange = vi.fn();
    render(
      <ExportDialog
        open
        onOpenChange={onOpenChange}
        documentId="d1"
        includedCount={1}
        excludedCount={0}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^Cancel$/ }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(fetchSpy).not.toHaveBeenCalled(); // dismissing is not a silent export
  });
});
