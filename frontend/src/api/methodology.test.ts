import { describe, expect, it } from "vitest";

import { toMethodology, type MethodologyPayload } from "./methodology";

// Ответ GET /api/v1/methodology на main (docs/api-contract.md), без справочных полей.
const payload: MethodologyPayload = {
  version: "v2",
  categories: [
    { code: "security", label: "Безопасность", weight: 25 },
    { code: "cicd", label: "CI/CD", weight: 20 },
  ],
  scoreLimits: [{ code: "security-open-critical", maximumScore: 60 }],
};

describe("toMethodology", () => {
  it("берёт веса и названия из ответа backend и добавляет объяснения", () => {
    const methodology = toMethodology(payload);
    expect(methodology.version).toBe("v2");
    expect(methodology.categories.map((category) => [category.code, category.weight])).toEqual([
      ["security", 25],
      ["cicd", 20],
    ]);
    expect(methodology.categories[1].measures).toContain("автоматических прогонов");
  });

  it("находит ограничение за критическую уязвимость в scoreLimits", () => {
    expect(toMethodology(payload).criticalScoreLimit).toBe(60);
    expect(toMethodology({ ...payload, scoreLimits: [] }).criticalScoreLimit).toBeNull();
    expect(toMethodology({ version: "v2", categories: [] }).criticalScoreLimit).toBeNull();
  });

  it("незнакомую категорию не описывает за backend", () => {
    const methodology = toMethodology({ version: "v3", categories: [{ code: "bus_factor", label: "Bus factor", weight: 10 }] });
    expect(methodology.categories[0]).toMatchObject({ label: "Bus factor", measures: "", caveat: "" });
  });

  it("подставляет политику пересчёта из docs/scheduling-policy.md", () => {
    expect(toMethodology(payload).schedule).toMatchObject({ regularHours: 24, activeHours: 6, inactiveHours: 72 });
  });

  it("активность в v2 учитывает недели с коммитами", () => {
    const activity = toMethodology({ ...payload, categories: [{ code: "activity", label: "Активность", weight: 15 }] })
      .categories[0];
    expect(activity.measures).toContain("неделях за полгода были коммиты");
    expect(activity.caveat).not.toContain("не считаем");
  });

  it("без формулы Security Score безопасность объясняется общими словами", () => {
    const security = toMethodology(payload).categories[0];
    expect(security.measures).toContain("с учётом их критичности");
    expect(security.caveat).toContain("все три сканера вернули полные результаты");
  });

  it("с формулой Security Score из backend объясняет штрафы по критичности", () => {
    const security = toMethodology({
      ...payload,
      security: {
        eligibility:
          "Все три движка должны вернуть полный результат с известными severity и status; иначе категория имеет статус insufficient_sample и не участвует в Score.",
        severityPenalties: [
          { severity: "CRITICAL", penaltyPerFinding: 60, maximumFindings: 2 },
          { severity: "HIGH", penaltyPerFinding: 15, maximumFindings: 3 },
          { severity: "MEDIUM", penaltyPerFinding: 5, maximumFindings: 4 },
          { severity: "LOW", penaltyPerFinding: 1, maximumFindings: 10 },
          { severity: "INFO", penaltyPerFinding: 0, maximumFindings: 0 },
        ],
      },
    }).categories[0];
    expect(security.measures).toContain("критичная — 60 (учитываем до 2)");
    expect(security.measures).toContain("низкая — 1 (учитываем до 10)");
    // Без штрафа INFO не упоминается.
    expect(security.measures).not.toContain("INFO");
    // Условие допуска — своими словами, без кодов контракта.
    expect(security.caveat).toContain("все три сканера вернули полные результаты");
    expect(security.caveat).not.toContain("insufficient_sample");
  });
});
