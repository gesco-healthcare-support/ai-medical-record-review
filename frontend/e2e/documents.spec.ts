import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { registerAndLogin } from "./support";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_PDF = path.join(dirname, "fixtures", "sample.pdf");

test("upload a record, open it, then delete it", async ({ page }) => {
  await registerAndLogin(page);

  // Upload via the hidden file input (setInputFiles drives hidden inputs directly). The fixture's
  // name carries no patient ID, so the upload asks for one first and sends nothing until it is given.
  await page.locator('input[type="file"]').setInputFiles(SAMPLE_PDF);
  const ask = page.getByRole("dialog", { name: "Patient ID needed" });
  await expect(ask).toBeVisible();
  await ask.getByLabel("Patient ID").fill("AB1234");
  await ask.getByRole("button", { name: "Upload" }).click();
  await expect(ask).toHaveCount(0);
  await expect(page.getByText("sample.pdf")).toBeVisible();

  // Open the record -> the review route renders (fresh upload -> the identify start panel), with the
  // patient ID that was typed. (Not the other-records count: specs run side by side on one database.)
  await page.getByText("sample.pdf").click();
  await expect(page).toHaveURL(/\/records\//);
  await expect(page.getByRole("heading", { name: "Ready to identify documents" })).toBeVisible();
  await expect(page.getByLabel("Patient ID")).toHaveValue("AB1234");

  // Back on My documents, delete via the row menu + confirm dialog -> the row is gone.
  await page.goto("/");
  await expect(page.getByText("sample.pdf")).toBeVisible();
  await page.getByRole("button", { name: "Actions" }).first().click();
  await page.getByRole("menuitem", { name: "Delete..." }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText("sample.pdf")).toHaveCount(0);
});
