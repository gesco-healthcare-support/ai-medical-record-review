import { expect, test } from "@playwright/test";

import { registerAndLogin, userMenuTrigger } from "./support";

test("a non-admin user has no Admin menu item", async ({ page }) => {
  await registerAndLogin(page);
  await userMenuTrigger(page).click();
  await expect(page.getByRole("menuitem", { name: "Diagnostic & Operative" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Admin" })).toHaveCount(0);
});

test("an authenticated user can reach the bundle pages", async ({ page }) => {
  await registerAndLogin(page);

  await page.goto("/diagnostics");
  await expect(page).toHaveURL(/\/diagnostics/);
  await expect(page.getByRole("heading", { name: "Diagnostic & Operative builder" })).toBeVisible();

  await page.goto("/depositions");
  await expect(page).toHaveURL(/\/depositions/);
  await expect(page.getByRole("heading", { name: "Depositions builder" })).toBeVisible();
});
