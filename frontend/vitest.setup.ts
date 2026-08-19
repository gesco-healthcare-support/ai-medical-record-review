// Adds jest-dom matchers (toBeInTheDocument, etc.) to Vitest's expect and augments its types, then
// unmounts React trees after each test so component tests stay isolated.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node 26 installs an EXPERIMENTAL `localStorage` accessor on the global object, and it resolves to
// `undefined` unless the process was started with --localstorage-file. Under the jsdom environment the
// global IS `window`, so that accessor occupies the slot jsdom would otherwise fill and jsdom never
// installs its own - `globalThis.localStorage` and `window.localStorage` are the same undefined
// property. Any component reading the bare `localStorage` identifier then dies on
// "Cannot read properties of undefined (reading 'getItem')".
//
// Measured on Node 26.4.0: 42 tests across 3 files - every suite that renders SplitPane, which reads
// localStorage to restore its divider position. CI stayed green throughout because it pins Node 24,
// where the accessor does not exist. There is no .nvmrc or package.json "engines" range to warn anyone
// off, so this failed silently for anyone on a current Node.
//
// Install a minimal in-memory Storage when the slot is empty. The accessor is `configurable: true`, so
// this is a redefine rather than a fight. On Node 24 the guard is false and jsdom's own storage stands.
if (!globalThis.localStorage) {
  const memoryStorage = (): Storage => {
    const map = new Map<string, string>();
    return {
      get length() {
        return map.size;
      },
      clear: () => map.clear(),
      getItem: (key: string) => (map.has(key) ? (map.get(key) as string) : null),
      key: (index: number) => Array.from(map.keys())[index] ?? null,
      removeItem: (key: string) => void map.delete(key),
      setItem: (key: string, value: string) => void map.set(key, String(value)),
    } as Storage;
  };
  for (const name of ["localStorage", "sessionStorage"] as const) {
    Object.defineProperty(globalThis, name, {
      value: memoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}

afterEach(() => {
  cleanup();
  // The shim above is a plain Map that outlives a single test, where previously there was no storage
  // at all. Leaving it populated leaks state between tests in a file, which is how adding the shim
  // turned a passing admin suite red. Clear both so every test starts from empty - jsdom's own storage
  // needs this too, so the reset is unconditional rather than tied to the shim.
  try {
    globalThis.localStorage?.clear();
    globalThis.sessionStorage?.clear();
  } catch {
    // A storage that refuses to clear is not worth failing a test over.
  }
});
