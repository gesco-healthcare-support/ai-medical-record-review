import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadBundlePdf } from "@/lib/bundle-api";

/** The category-bundle download had no tests at all, despite owning the filename the reviewer ends
 *  up with on disk. These cover the three outcomes that differ: the server names the file, it does
 *  not, or it refuses. Synthetic names only. */
const CONFIG = {
  label: "Diagnostic and Operative",
  slug: "diagnostic-operative",
  categories: ["3", "4"],
};

let downloaded: string[] = [];

function respond(status: number, headers: Record<string, string>, body?: unknown) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (k: string) => headers[k] ?? null },
    blob: async () => new Blob(["stub"]),
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  downloaded = [];
  Object.defineProperty(URL, "createObjectURL", {
    value: vi.fn(() => "blob:stub"),
    configurable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    downloaded.push(this.download);
  });
});

afterEach(() => vi.restoreAllMocks());

describe("downloadBundlePdf", () => {
  it("takes the filename the server sent in Content-Disposition", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(200, { "Content-Disposition": 'attachment; filename="records 3-4.pdf"' }),
      ),
    );
    await downloadBundlePdf("doc-1", CONFIG);
    expect(downloaded).toEqual(["records 3-4.pdf"]);
  });

  it("falls back to the bundle slug when the server names nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(200, {})));
    await downloadBundlePdf("doc-1", CONFIG);
    expect(downloaded).toEqual(["diagnostic-operative.pdf"]);
  });

  it("raises the server's own reason rather than a bare status code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond(409, {}, { detail: "no matching documents in this record" })),
    );
    await expect(downloadBundlePdf("doc-1", CONFIG)).rejects.toThrow(/no matching documents/);
    expect(downloaded).toEqual([]);
  });
});
