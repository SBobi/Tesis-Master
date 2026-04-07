import { expect, test } from "@playwright/test";

test("home renderiza narrativa principal", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Operate repair jobs from one workspace.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Cases" })).toBeVisible();
});
