import { expect, test } from "@playwright/test";

test("home renderiza narrativa principal", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("From breaking update", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "Explore the pipeline" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Cases", exact: true })).toBeVisible();
});
