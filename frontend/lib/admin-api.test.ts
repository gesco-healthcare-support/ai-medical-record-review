/**
 * The admin client calls.
 *
 * These are the only calls that reach another owner's data - reprocess re-summarizes any owner's
 * document - so the `/admin` prefix is not decoration, it is the route the server gates on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createCategory,
  deletePrompt,
  getPrompt,
  listCategories,
  putPrompt,
  reprocessDocument,
  updateCategory,
} from "@/lib/admin-api";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall() {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return { url: url as string, init: init as RequestInit };
}

describe("the category catalog", () => {
  it("reads, creates and updates under the admin route", async () => {
    await listCategories();
    expect(lastCall().url).toBe("/api/admin/categories");

    await createCategory({ id: "9", name: "New category" } as never);
    expect(lastCall().url).toBe("/api/admin/categories");
    expect(lastCall().init.method).toBe("POST");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ id: "9", name: "New category" });

    await updateCategory("3", { name: "Renamed" });
    expect(lastCall().url).toBe("/api/admin/categories/3");
    // PATCH, not PUT: the body is a PARTIAL, and a PUT would invite the server to treat the
    // unsent fields as cleared.
    expect(lastCall().init.method).toBe("PATCH");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ name: "Renamed" });
  });
});

describe("prompts", () => {
  it("reads, writes and reverts one category's prompt", async () => {
    await getPrompt("3");
    expect(lastCall().url).toBe("/api/admin/prompts/3");

    await putPrompt("3", "A custom prompt.");
    expect(lastCall().url).toBe("/api/admin/prompts/3");
    expect(lastCall().init.method).toBe("PUT");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ text: "A custom prompt." });

    // Reverting is a DELETE of the custom row, which is what lets the built-in code prompt apply
    // again - sending an empty string instead would store "no prompt" rather than "use the default".
    await deletePrompt("3");
    expect(lastCall().url).toBe("/api/admin/prompts/3");
    expect(lastCall().init.method).toBe("DELETE");
  });
});

describe("reprocess", () => {
  it("posts to the admin-scoped path", async () => {
    // This one re-summarizes ANY owner's document. The /admin prefix is what the server authorises
    // on; the same action on the ordinary route would only ever reach the caller's own records.
    await reprocessDocument("d1");

    expect(lastCall().url).toBe("/api/admin/reprocess/d1");
    expect(lastCall().init.method).toBe("POST");
  });
});
