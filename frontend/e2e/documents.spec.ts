import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { registerAndLogin } from "./support";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_PDF = path.join(dirname, "fixtures", "sample.pdf");

test("upload a record, open it, then delete it", async ({ page }) => {
  await registerAndLogin(page);

  // Upload via the hidden file input (setInputFiles drives hidden inputs directly).
  await page.locator('input[type="file"]').setInputFiles(SAMPLE_PDF);
  await expect(page.getByText("sample.pdf")).toBeVisible();

  // Open the record -> the review route renders (fresh upload -> the identify start panel).
  await page.getByText("sample.pdf").click();
  await expect(page).toHaveURL(/\/records\//);
  await expect(page.getByRole("heading", { name: "Ready to identify documents" })).toBeVisible();

  // Back on My documents, delete via the row menu + confirm dialog -> the row is gone.
  await page.goto("/");
  await expect(page.getByText("sample.pdf")).toBeVisible();
  await page.getByRole("button", { name: "Actions" }).first().click();
  await page.getByRole("menuitem", { name: "Delete..." }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText("sample.pdf")).toHaveCount(0);
});
