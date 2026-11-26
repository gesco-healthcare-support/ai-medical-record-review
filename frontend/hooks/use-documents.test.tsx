/**
 * The documents list and the four mutations that change it.
 *
 * Two guarantees are worth having here. The list POLLS only while something is actually running -
 * polling for ever is a request every two seconds per open tab, and never polling means a finished
 * identification sits there looking unfinished. And every mutation must refetch the list, because
 * each of them changes what the list should say and the screen has no other way to find out.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const listDocuments = vi.fn();
const listTeamQueue = vi.fn();
const getBillingSheet = vi.fn();
vi.mock("@/lib/documents-api", () => ({
  listDocuments: (...args: unknown[]) => listDocuments(...args),
  listTeamQueue: (...args: unknown[]) => listTeamQueue(...args),
  combineReceivedFiles: vi.fn().mockResolvedValue({ id: "d2", page_count: 4 }),
  deleteDocument: vi.fn().mockResolvedValue({ ok: true }),
  startIdentification: vi.fn().mockResolvedValue({ ok: true }),
  setCompleted: vi.fn().mockResolvedValue({}),
  listDoctors: vi.fn().mockResolvedValue({ doctors: ["Alpha", "Beta"] }),
  getBillingSheet: (...args: unknown[]) => getBillingSheet(...args),
  saveBillingSheet: vi.fn(),
}));
vi.mock("@/lib/admin-api", () => ({
  assignDocument: vi.fn().mockResolvedValue({ id: "d1", owner: { id: 7, name: "Brian" } }),
}));

import { assignDocument } from "@/lib/admin-api";
import {
  combineReceivedFiles,
  deleteDocument,
  listDoctors,
  saveBillingSheet,
  setCompleted,
  startIdentification,
} from "@/lib/documents-api";
import {
  BILLING_POLL_MS,
  useAssignDocument,
  useBillingSheet,
  useCombineReceivedFiles,
  useDoctors,
  useSaveBillingSheet,
  useDeleteDocument,
  useDocuments,
  useSetCompleted,
  useStartIdentification,
  useTeamQueue,
} from "@/hooks/use-documents";

function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated: unknown[] = [];
  vi.spyOn(client, "invalidateQueries").mockImplementation(async (filters) => {
    invalidated.push(filters?.queryKey);
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, invalidated };
}

const doc = (over: Record<string, unknown> = {}) => ({
  id: "d1",
  name: "record.pdf",
  active_job: null,
  rows_count: 3,
  ...over,
});

afterEach(() => {
  vi.useRealTimers();
  listDocuments.mockReset();
  listTeamQueue.mockReset();
});

describe("useTeamQueue", () => {
  it("reads the team queue, under the documents key every mutation refreshes", async () => {
    listTeamQueue.mockResolvedValue([doc({ in_team_queue: true })]);
    const { wrapper } = harness();
    const { result } = renderHook(() => useTeamQueue(), { wrapper });

    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(listTeamQueue).toHaveBeenCalledTimes(1);
  });
});

describe("useDocuments polling", () => {
  it("keeps polling while a record has a job running", async () => {
    vi.useFakeTimers();
    listDocuments.mockResolvedValue([doc({ active_job: "segment" })]);
    const { wrapper } = harness();

    renderHook(() => useDocuments(), { wrapper });
    await vi.waitFor(() => expect(listDocuments).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(2000);
    await vi.waitFor(() => expect(listDocuments).toHaveBeenCalledTimes(2));
  });

  it("stops polling once nothing is running", async () => {
    vi.useFakeTimers();
    listDocuments.mockResolvedValue([doc({ active_job: null })]);
    const { wrapper } = harness();

    renderHook(() => useDocuments(), { wrapper });
    await vi.waitFor(() => expect(listDocuments).toHaveBeenCalledTimes(1));

    // Well past the 2s interval the running case uses: an idle list must cost nothing.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listDocuments).toHaveBeenCalledTimes(1);
  });
});

describe("useDocuments mutations", () => {
  it("refetches the list after every mutation, and each calls its own endpoint", async () => {
    // The four hooks are one-line wrappers over a shared factory, so what is worth asserting is
    // that EACH of them refetches and that each reaches its own call - a wrapper pointed at the
    // wrong api function would refetch just as happily and delete a record the reviewer uploaded.
    listDocuments.mockResolvedValue([]);
    const combined = {
      lastName: "Synthetic",
      firstName: "Patient",
      patientId: "801234",
      dateReceived: "2026-11-03",
      appointmentDate: "2026-12-01",
      recordsFrom: "applicant" as const,
      files: [{ token: "t1", kind: "records" as const }],
      owner: 7,
    };

    const cases = [
      { hook: useCombineReceivedFiles, args: combined },
      { hook: useAssignDocument, args: { id: "d1", owner: 7, allowDuplicate: false } },
      { hook: useDeleteDocument, args: "d1" },
      { hook: useStartIdentification, args: "d1" },
    ];

    for (const { hook, args } of cases) {
      const { wrapper, invalidated } = harness();
      const { result } = renderHook(() => hook(), { wrapper });
      await result.current.mutateAsync(args as never);
      await waitFor(() => expect(invalidated).toEqual([["documents"]]));
    }

    expect(combineReceivedFiles).toHaveBeenCalledWith(combined);
    // Assigning moves a record OUT of the list on screen, so it must refetch like the rest.
    expect(assignDocument).toHaveBeenCalledWith("d1", 7, false);
    expect(deleteDocument).toHaveBeenCalledWith("d1");
    expect(startIdentification).toHaveBeenCalledWith("d1");
  });
});

describe("useSetCompleted", () => {
  it("shows the mark at once, calls the endpoint, and reads the list again", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidated: unknown[] = [];
    vi.spyOn(client, "invalidateQueries").mockImplementation(async (filters) => {
      invalidated.push(filters?.queryKey);
    });
    client.setQueryData(["documents"], [doc(), doc({ id: "d2" })]);
    client.setQueryData(["documents", "owner", 7], [doc()]);
    let answer: (value: unknown) => void = () => undefined;
    vi.mocked(setCompleted).mockImplementationOnce(
      () => new Promise((resolve) => (answer = resolve)) as never,
    );
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useSetCompleted(), { wrapper });

    result.current.mutate({ id: "d1", completed: true });

    // Before the server answers, every cached list shows the mark on that record only.
    await waitFor(() => {
      const mine = client.getQueryData<Array<Record<string, unknown>>>(["documents"]);
      expect(mine?.[0].completed_at).toBeTruthy();
      expect(mine?.[1].completed_at).toBeUndefined();
    });
    const theirs = client.getQueryData<Array<Record<string, unknown>>>(["documents", "owner", 7]);
    expect(theirs?.[0].completed_at).toBeTruthy();
    expect(setCompleted).toHaveBeenCalledWith("d1", true);

    answer({});
    await waitFor(() => expect(invalidated).toEqual([["documents"]]));
  });

  it("clears the mark the same way", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    client.setQueryData(["documents"], [doc({ completed_at: "2026-11-05T10:00:00Z" })]);
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useSetCompleted(), { wrapper });

    await result.current.mutateAsync({ id: "d1", completed: false });

    expect(setCompleted).toHaveBeenCalledWith("d1", false);
    const mine = client.getQueryData<Array<Record<string, unknown>>>(["documents"]);
    expect(mine?.[0].completed_at).toBeNull();
  });
});

describe("the billing sheet hooks", () => {
  afterEach(() => getBillingSheet.mockReset());

  it("reads the doctors only while asked to", async () => {
    const { wrapper } = harness();
    const off = renderHook(() => useDoctors(false), { wrapper });
    expect(off.result.current.fetchStatus).toBe("idle");
    expect(listDoctors).not.toHaveBeenCalled();

    const on = renderHook(() => useDoctors(true), { wrapper });
    await waitFor(() => expect(on.result.current.data).toEqual(["Alpha", "Beta"]));
  });

  it("reads no sheet without a record", () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useBillingSheet(null), { wrapper });

    expect(result.current.fetchStatus).toBe("idle");
    expect(getBillingSheet).not.toHaveBeenCalled();
  });

  it("reads the sheet again while the record's pages are read, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getBillingSheet
      .mockResolvedValueOnce({ pages_unread: 4, counted: null })
      .mockResolvedValue({ pages_unread: 0, counted: { office: 4, depo: 0 } });
    const { wrapper } = harness();
    const { result } = renderHook(() => useBillingSheet("d1"), { wrapper });
    await waitFor(() => expect(result.current.data?.pages_unread).toBe(4));

    await vi.advanceTimersByTimeAsync(BILLING_POLL_MS);
    await waitFor(() => expect(result.current.data?.pages_unread).toBe(0));
    const calls = getBillingSheet.mock.calls.length;
    await vi.advanceTimersByTimeAsync(BILLING_POLL_MS * 2);

    expect(getBillingSheet.mock.calls.length).toBe(calls);
    expect(getBillingSheet).toHaveBeenCalledWith("d1");
  });

  it("puts the saved sheet in place of the cached one", async () => {
    const saved = { pages_unread: 0, discrepancy_reason: "Two pages missing" };
    vi.mocked(saveBillingSheet).mockResolvedValueOnce(saved as never);
    getBillingSheet.mockResolvedValue({ pages_unread: 0, discrepancy_reason: "" });
    const { wrapper } = harness();
    const sheet = renderHook(() => useBillingSheet("d1"), { wrapper });
    await waitFor(() => expect(sheet.result.current.data).toBeDefined());
    const save = renderHook(() => useSaveBillingSheet("d1"), { wrapper });

    await save.result.current.mutateAsync({ discrepancy_reason: "Two pages missing" } as never);

    expect(saveBillingSheet).toHaveBeenCalledWith("d1", { discrepancy_reason: "Two pages missing" });
    await waitFor(() => expect(sheet.result.current.data).toEqual(saved));
  });
});
