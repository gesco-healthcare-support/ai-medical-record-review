import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { DOWNLOAD_INTERRUPTED, DOWNLOAD_NOT_PREPARED, downloadFile } from "@/lib/download";
import { humanizeError } from "@/lib/errors";

/** #389: an export's POST answers with WHERE to download the file it built, and the browser fetches that
 *  address itself - so its own download manager streams the body to disk. The page used to read the whole
 *  file into a Blob, which Chrome cancels part-way on a machine short of disk space; these tests pin that it
 *  never does again. Synthetic names only. */
const PREPARED = {
  token: "tok",
  filename: "Synthetic_Record_Medical_Records_summary.docx",
  size: 4,
  url: "/api/documents/doc-1/downloads/tok",
};

let clicked: { href: string; download: string }[] = [];
const blob = vi.fn(async () => new Blob(["stub"]));

function respond(status: number, body?: unknown) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => null },
    blob,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  clicked = [];
  blob.mockClear();
  // Every failure path logs (#390); silenced here so the run stays readable, asserted where it matters.
  vi.spyOn(console, "error").mockImplementation(() => {});
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), configurable: true });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicked.push({ href: this.getAttribute("href") ?? "", download: this.download });
  });
});

afterEach(() => vi.restoreAllMocks());

describe("downloadFile", () => {
  it("starts a native download of the address the server returned, under its name", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(200, PREPARED)));

    await downloadFile("/documents/doc-1/export", {}, "fallback.docx");

    expect(clicked).toEqual([{ href: PREPARED.url, download: PREPARED.filename }]);
  });

  it("never holds the file in the page - no Blob, no object URL", async () => {
    // The whole point of #389. A large Blob is what Chrome drops on a low-disk machine.
    vi.stubGlobal("fetch", vi.fn(async () => respond(200, PREPARED)));

    await downloadFile("/documents/doc-1/export/pdf", {}, "fallback.pdf");

    expect(blob).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("falls back to the caller's name when the server names nothing", async () => {
    const { filename: _dropped, ...unnamed } = PREPARED;
    vi.stubGlobal("fetch", vi.fn(async () => respond(200, unnamed)));

    await downloadFile("/documents/doc-1/export", {}, "fallback.docx");

    expect(clicked).toEqual([{ href: PREPARED.url, download: "fallback.docx" }]);
  });

  it("posts the export's fields as JSON with the session cookie", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, PREPARED));
    fetchSpy.mockClear();

    await downloadFile("/documents/doc-1/export/memo", { patientName: "Synthetic Patient" }, "x");

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/documents/doc-1/export/memo");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include");
    expect(JSON.parse(String(init?.body))).toEqual({ patientName: "Synthetic Patient" });
  });

  it("rejects with the server's own reason and starts no download", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond(422, { detail: "One document could not be read." })),
    );

    const err = await downloadFile("/documents/doc-1/export", {}, "x").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(humanizeError(err, { fallback: "Export failed." })).toBe(
      "One document could not be read.",
    );
    expect(clicked).toEqual([]);
  });

  it("rejects when the session has ended, and starts no download", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(401, {})));

    await expect(downloadFile("/documents/doc-1/export", {}, "x")).rejects.toThrow(/signed out/);
    expect(clicked).toEqual([]);
  });

  it("names an answer cut off after its headers as interrupted, not 'Export failed.'", async () => {
    // #390: headers arrived, then the body could not be read - the case that used to fall through to the
    // dialog's generic fallback and hide the cause.
    const cut = {
      status: 200,
      ok: true,
      headers: { get: (name: string) => (name.toLowerCase() === "content-length" ? "120" : null) },
      blob,
      json: async () => {
        throw new TypeError("network error");
      },
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn(async () => cut));

    const err = await downloadFile("/documents/doc-1/export", {}, "x").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(humanizeError(err, { fallback: "Export failed." })).toBe(DOWNLOAD_INTERRUPTED);
    expect(clicked).toEqual([]);
    expect(console.error).toHaveBeenCalledWith("download failed", {
      phase: "prepare",
      status: 200,
      expectedBytes: 120,
    });
  });

  it("names a bare 502 or 504 from the proxy instead of 'Export failed.'", async () => {
    // nginx's own error page is HTML, so there is no server sentence to show.
    for (const status of [502, 504]) {
      const html = {
        status,
        ok: false,
        headers: { get: () => null },
        json: async () => {
          throw new SyntaxError("Unexpected token <");
        },
      } as unknown as Response;
      vi.stubGlobal("fetch", vi.fn(async () => html));

      const err = await downloadFile("/documents/doc-1/export/zip", {}, "x").catch((e: unknown) => e);

      expect(humanizeError(err, { fallback: "Export failed." })).toBe(DOWNLOAD_NOT_PREPARED);
    }
  });

  it("keeps the server's own sentence on a 502 that has one", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(502, { detail: "The AI service did not answer." })));

    const err = await downloadFile("/documents/doc-1/export", {}, "x").catch((e: unknown) => e);

    expect(humanizeError(err, { fallback: "Export failed." })).toBe("The AI service did not answer.");
  });

  it("logs each failure's phase and status to the console, and never the file's name", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(422, { detail: "One document could not be read." })));

    await downloadFile("/documents/doc-1/export", {}, "fallback.docx").catch(() => undefined);

    expect(console.error).toHaveBeenCalledWith("download failed", {
      phase: "prepare",
      status: 422,
      expectedBytes: null,
    });
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toContain(PREPARED.filename);
  });

  it("reports a transport failure as status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    const err = await downloadFile("/documents/doc-1/export", {}, "x").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
    expect(console.error).toHaveBeenCalledWith("download failed", {
      phase: "prepare",
      status: 0,
      expectedBytes: null,
    });
  });
});
