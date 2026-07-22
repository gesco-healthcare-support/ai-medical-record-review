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
    include: ["{lib,hooks,components}/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["lib/**", "hooks/**", "components/**"],
      exclude: ["**/*.{test,spec}.{ts,tsx}"],
    },
  },
});
