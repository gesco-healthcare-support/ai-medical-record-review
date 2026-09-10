import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { downloadBundlePdf } from "@/lib/bundle-api";
import { humanizeError } from "@/lib/errors";

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

  it("raises it as an ApiError, so the screen shows the reason and not a fallback", async () => {
    // The test above pins that the reason is READ. It was then thrown as a plain `Error`, and
    // `humanizeError` discards a plain Error by design (errors.test.ts pins that too) - so the
    // module extracted the server's words and the reviewer saw "The download failed." Two correct
    // tests, one broken delivery; this one covers the join between them.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond(409, {}, { detail: "no matching documents in this record" })),
    );
    await expect(downloadBundlePdf("doc-1", CONFIG)).rejects.toBeInstanceOf(ApiError);

    const err = await downloadBundlePdf("doc-1", CONFIG).catch((e: unknown) => e);
    expect(humanizeError(err, { fallback: "The download failed." })).toBe(
      "no matching documents in this record",
    );
  });

  it("rejects when the session has ended, rather than resolving", async () => {
    // A 401 redirected and RETURNED, so `runBundleDownload` saw a resolved promise and reported
    // "Combined PDF downloaded." while the browser was navigating to /login.
    vi.stubGlobal("fetch", vi.fn(async () => respond(401, {}, {})));
    await expect(downloadBundlePdf("doc-1", CONFIG)).rejects.toThrow(/signed out/);
    expect(downloaded).toEqual([]);
  });

  it("reports a transport failure as one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const err = await downloadBundlePdf("doc-1", CONFIG).catch((e: unknown) => e);
    expect(humanizeError(err)).toMatch(/couldn't reach the server/i);
  });
});
