import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

// Unit + component tests for the app's logic, hooks, and client components. tsconfigPaths resolves
// the `@/*` alias; react() enables JSX + Fast Refresh transforms; jsdom gives a DOM for Testing
// Library. Playwright E2E specs live under e2e/ and are run by playwright, not vitest.
export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // One worker keeps its jsdom environment across the files it runs, instead of building a fresh
    // one per file - the environment was as large as the tests themselves in CI (41s of 106s summed).
    // Measured 2026-09-23, coverage on, same window: 26-35s against 55-59s for forks + isolation.
    //
    // Sharing a worker is only safe because of four guards, one per thing that would otherwise leak
    // from one file into the next:
    //   - modules: vitest.setup.ts resets the module cache at the start of every file. Without it a
    //     module first imported under file A's vi.mock stays wired to A's mock for file B - shuffled
    //     file orders failed 6 and 32 tests that way.
    //   - globals: `unstubGlobals` undoes every vi.stubGlobal before each test. Load-bearing, not a
    //     precaution: with it off, one shuffled single-worker order failed 7 tests
    //     (lib/bundle-api.test.ts, for one, stubs fetch and never restores it).
    //   - the DOM: vitest.setup.ts unmounts after every test, as before.
    //   - fake clocks: vitest never switches them off between files, so vitest.setup.ts fails any file
    //     that ends with fake timers still on, after switching real timers back on.
    // Not guarded - restore these in the same file:
    //   - anything set directly on window or document rather than through vi.stubGlobal: the next
    //     file runs in the same window and sees it;
    //   - a redefinition on a prototype or a built-in (Object.defineProperty);
    //   - module state inside a node_modules package (a Testing Library configure() call, say): the
    //     module reset does not reach packages, which load once per worker.
    pool: "threads",
    isolate: false,
    unstubGlobals: true,
    // `app` is in this list because it is in the COVERAGE list below. Without it a test written
    // under app/ is never collected: the run passes having executed nothing, the coverage number
    // does not move, and nothing anywhere reports a problem. Measuring a directory the runner
    // cannot collect tests for is the one combination that fails silently.
    include: ["{app,lib,hooks,components}/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e/**"],
    coverage: {
      provider: "v8",
      // `json-summary` carries a statements metric; lcov does NOT (it has lines, functions and
      // branches only). The CI coverage floor gates all four, so it cannot be computed from lcov
      // alone. `text` stays for the local run, `lcov` for SonarCloud.
      reporter: ["text", "lcov", "json-summary"],
      // `app/**` is measured even though nothing tests it yet. The route tree is real shipped
      // code, so leaving it out reports a coverage figure for a subset of the app while reading
      // as if it were the whole of it. An honest denominator that starts lower is worth more
      // than a flattering one, and an untested file counted here can only ever raise the number
      // as tests arrive - it cannot hide.
      include: ["lib/**", "hooks/**", "components/**", "app/**"],
      exclude: ["**/*.{test,spec}.{ts,tsx}"],
    },
  },
});
