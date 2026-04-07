import { expect, test } from "@playwright/test";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const KNOWN_CASE_ID = "3407b237-981f-40da-9623-4c4ac3c2087b";

test.beforeEach(async ({ request }) => {
  const health = await request.get(`${API_BASE}/api/health`);
  test.skip(!health.ok(), "Backend API no disponible para pruebas integradas.");
});

test("detalle de caso muestra paneles operativos", async ({ page }) => {
  await page.goto(`/cases/${KNOWN_CASE_ID}`);

  await expect(page.getByRole("heading", { name: "Run Configuration" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Validation Matrix" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Explanation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Execution Metrics" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Artifacts" })).toBeVisible();

  await expect(page.getByRole("button", { name: "Run selected modes" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Validate attempt" })).toBeVisible();
  await expect(page.getByText("Selected mode", { exact: true })).toBeVisible();
  await expect(page.getByText("Case not found")).toHaveCount(0);
});
