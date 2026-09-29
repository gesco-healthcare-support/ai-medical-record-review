// ESLint flat config. eslint-config-next 15 ships its rules as legacy "extends" configs, so
// FlatCompat loads them (the setup Next 15's own docs give). CI runs `pnpm lint`, which fails on
// any error or warning (--max-warnings 0).
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const eslintConfig = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
      // The vendored PDF.js viewer build: third-party code under its own licence, not ours to lint.
      "public/pdfjs/**",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // Destructuring a property out to drop it (`const { filename: _dropped, ...rest } = x`) is
      // the idiom for "everything except"; the dropped name is unused by design.
      "@typescript-eslint/no-unused-vars": ["warn", { ignoreRestSiblings: true }],
    },
  },
];

export default eslintConfig;
