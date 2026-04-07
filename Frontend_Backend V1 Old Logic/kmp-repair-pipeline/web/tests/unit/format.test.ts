import { describe, expect, it } from "vitest";

import { formatDate, metric, shortId } from "@/lib/format";

describe("format helpers", () => {
  it("shortId corta a 8 por defecto", () => {
    expect(shortId("1234567890")).toBe("12345678");
  });

  it("metric muestra N/A para null", () => {
    expect(metric(null)).toBe("N/A");
  });

  it("formatDate retorna string legible", () => {
    const output = formatDate("2026-04-05T12:00:00Z");
    expect(output.length).toBeGreaterThan(4);
  });
});
