import { expect, type Page } from "@playwright/test";

// Each spec registers its OWN fresh account so uploads/state never collide across specs (they
// share one backend DB). example.com is the RFC 2606 reserved domain the email validator accepts.
export function uniqueEmail(): string {
  return `e2e+${Date.now()}${Math.floor(Math.random() * 1e6)}@example.com`;
}

// Meets the register rule: >= 8 chars, a digit, a symbol. Synthetic - never a real credential.
export const PASSWORD = "E2eTest#2026";

/** Register a new account (which auto-logs in) and wait for the signed-in home screen. */
export async function registerAndLogin(page: Page, name = "E2E Tester") {
  const email = uniqueEmail();
  await page.goto("/login?view=register");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Start your first review" })).toBeVisible();
  return { email, name };
}
