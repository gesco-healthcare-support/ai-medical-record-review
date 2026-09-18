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
