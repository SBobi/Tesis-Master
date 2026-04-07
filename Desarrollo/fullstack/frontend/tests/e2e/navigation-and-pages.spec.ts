import { expect, test } from "@playwright/test";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

test.beforeEach(async ({ request }) => {
  const health = await request.get(`${API_BASE}/api/health`);
  test.skip(!health.ok(), "Backend API no disponible para pruebas integradas.");
});

test("navegacion superior abre rutas principales", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("link", { name: "Process", exact: true }).click();
  await expect(page).toHaveURL(/\/process$/);
  await expect(page.getByRole("heading", { name: /The Process/i })).toBeVisible();

  await page.getByRole("link", { name: "Cases", exact: true }).click();
  await expect(page).toHaveURL(/\/cases$/);
  await expect(page.getByRole("heading", { name: /Selected\s*Cases\./i })).toBeVisible();

  await page.getByRole("link", { name: "Results", exact: true }).click();
  await expect(page).toHaveURL(/\/results$/);
  await expect(page.getByRole("heading", { name: /Understanding/i })).toBeVisible();

  await page.getByRole("link", { name: "Environment", exact: true }).click();
  await expect(page).toHaveURL(/\/environment$/);
  await expect(page.getByRole("heading", { name: /Environment/i })).toBeVisible();

  await page.getByRole("link", { name: "About", exact: true }).click();
  await expect(page).toHaveURL(/\/about$/);
  await expect(page.getByRole("heading", { name: /About the Thesis/i })).toBeVisible();
});

test("cases valida formato de PR URL", async ({ page }) => {
  await page.goto("/cases");

  await page.getByPlaceholder("https://github.com/owner/repo/pull/42").fill("not-a-pr-url");
  await page.getByRole("button", { name: "Create Case" }).click();

  await expect(page.getByText("El PR URL debe tener formato")).toBeVisible();
});

test("results y environment consumen backend sin error", async ({ page }) => {
  await page.goto("/results");
  await expect(page.getByRole("heading", { name: "Aggregated Reports" })).toBeVisible();
  await expect(page.getByRole("button", { name: "CSV" })).toBeVisible();
  await expect(page.getByText("No se pudieron cargar los reportes")).toHaveCount(0);

  await page.goto("/environment");
  await expect(page.getByText("Health Checks")).toBeVisible();
  await expect(page.getByText("API / Database")).toBeVisible();
  await expect(page.getByText("No se pudo consultar el estado del backend")).toHaveCount(0);
});
