import { describe, expect, it } from "vitest";

import { scoreVerdict } from "./verdict";

describe("scoreVerdict", () => {
  it("делит оценку на три понятных состояния", () => {
    expect(scoreVerdict(92, false).title).toBe("Проект в хорошем состоянии");
    expect(scoreVerdict(80, false).title).toBe("Проект в хорошем состоянии");
    expect(scoreVerdict(73, false).title).toBe("Проект рабочий, но есть что поправить");
    expect(scoreVerdict(60, false).title).toBe("Проект рабочий, но есть что поправить");
    expect(scoreVerdict(59, false).title).toBe("Проект требует внимания");
  });

  it("предупреждает, когда посчитали не всё", () => {
    expect(scoreVerdict(73, true).note).toContain("предварительная");
    expect(scoreVerdict(73, false).note).toContain("полная");
  });

  it("отсутствие оценки не выдаёт за плохую оценку", () => {
    const verdict = scoreVerdict(null, true);
    expect(verdict.title).toBe("Оценку пока не из чего посчитать");
    expect(verdict.note).toContain("не значит");
  });
});
