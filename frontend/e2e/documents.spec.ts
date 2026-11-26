import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { registerAndLogin } from "./support";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_PDF = path.join(dirname, "fixtures", "sample.pdf");

test("upload a record, open it from the team queue, then delete it", async ({ page }) => {
  await registerAndLogin(page);

  // "Upload records" is the one upload: it opens the upload form, whose own file input takes the
  // fixture (setInputFiles drives hidden inputs directly).
  await page.getByRole("button", { name: "Upload records" }).click();
  const form = page.getByRole("dialog", { name: "Upload records" });
  await expect(form).toBeVisible();
  await form.locator('input[type="file"]').setInputFiles(SAMPLE_PDF);
  // The file is read on the server first; with no model in CI its first page is guessed by rules,
  // after at most the 15 seconds the server gives the model.
  await expect(form.getByText(/^\d+ pages?$/)).toBeVisible({ timeout: 30_000 });

  // A patient name no other run uses: specs share one database, and the team queue is everyone's.
  const lastName = `Example${Date.now()}`;
  const recordName = `${lastName}_Sample-AB1234-11032026.pdf`;
  await form.getByLabel("Patient last name").fill(lastName);
  await form.getByLabel("Patient first name").fill("Sample");
  await form.getByLabel("Patient ID").fill("AB1234");
  await form.getByLabel("Date received").fill("2026-11-03");
  await form.getByLabel("Records from").selectOption("applicant");
  await form.getByLabel("Type of visit").selectOption("QME");
  await form.getByLabel("Appointment date").fill("2026-12-01");
  await form.getByLabel("Interpreter").selectOption("no");
  await form.getByRole("button", { name: "Upload", exact: true }).click();
  // On a database that already holds the fixture (a local run), the form asks first.
  const anyway = form.getByRole("button", { name: "Upload anyway" });
  if (await anyway.isVisible()) await anyway.click();
  await expect(form).toHaveCount(0);

  // Every upload goes to the team queue, named the way the office names its files.
  const queue = page.getByRole("region", { name: "Team queue" });
  await expect(queue.getByText(recordName)).toBeVisible();

  // Open the record -> the review route renders (fresh upload -> the identify start panel), with the
  // patient ID that was typed. (Not the other-records count: specs run side by side on one database.)
  await queue.getByText(recordName).click();
  await expect(page).toHaveURL(/\/records\//);
  await expect(page.getByRole("heading", { name: "Ready to identify documents" })).toBeVisible();
  await expect(page.getByLabel("Patient ID")).toHaveValue("AB1234");

  // Back on My documents, the uploader deletes it from the queue via the row menu + confirm dialog.
  await page.goto("/");
  const row = queue.locator("tr", { hasText: recordName });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Actions" }).click();
  await page.getByRole("menuitem", { name: "Delete..." }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(queue.getByText(recordName)).toHaveCount(0);
});
