import { describe, expect, it } from "vitest";

import { toMethodology, type MethodologyPayload } from "./methodology";

// Ответ GET /api/v1/methodology на main (docs/api-contract.md), без справочных полей.
const payload: MethodologyPayload = {
  version: "v1",
  categories: [
    { code: "security", label: "Безопасность", weight: 25 },
    { code: "cicd", label: "CI/CD", weight: 20 },
  ],
  scoreLimits: [{ code: "security-open-critical", maximumScore: 60 }],
};

describe("toMethodology", () => {
  it("берёт веса и названия из ответа backend и добавляет объяснения", () => {
    const methodology = toMethodology(payload);
    expect(methodology.version).toBe("v1");
    expect(methodology.categories.map((category) => [category.code, category.weight])).toEqual([
      ["security", 25],
      ["cicd", 20],
    ]);
    expect(methodology.categories[1].measures).toContain("автоматических прогонов");
  });

  it("находит ограничение за критическую уязвимость в scoreLimits", () => {
    expect(toMethodology(payload).criticalScoreLimit).toBe(60);
    expect(toMethodology({ ...payload, scoreLimits: [] }).criticalScoreLimit).toBeNull();
    expect(toMethodology({ version: "v1", categories: [] }).criticalScoreLimit).toBeNull();
  });

  it("незнакомую категорию не описывает за backend", () => {
    const methodology = toMethodology({ version: "v2", categories: [{ code: "bus_factor", label: "Bus factor", weight: 10 }] });
    expect(methodology.categories[0]).toMatchObject({ label: "Bus factor", measures: "", caveat: "" });
  });

  it("подставляет политику пересчёта из docs/scheduling-policy.md", () => {
    expect(toMethodology(payload).schedule).toMatchObject({ regularHours: 24, activeHours: 6, inactiveHours: 72 });
  });
});
