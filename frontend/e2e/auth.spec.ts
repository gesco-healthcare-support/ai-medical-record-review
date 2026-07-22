import { expect, test } from "@playwright/test";

import { PASSWORD, registerAndLogin, userMenuTrigger } from "./support";

test("register, sign out, and sign back in", async ({ page }) => {
  const { email } = await registerAndLogin(page);

  // Sign out via the user menu -> back to the login screen.
  await userMenuTrigger(page).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  // Sign back in with the same credentials -> the signed-in home screen returns.
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Start your first review" })).toBeVisible();
});
