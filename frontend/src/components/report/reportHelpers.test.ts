import { describe, expect, it } from "vitest";

import type { ReportCategory } from "../../api/report";
import { biggestLosses, buildFormula, isMeasured, pickHighlights } from "./reportHelpers";

function measured(code: string, score: number, weight: number, measuredWeight: number): ReportCategory {
  const effectiveWeight = (weight / measuredWeight) * 100;
  return {
    code,
    label: code,
    status: "measured",
    score,
    weight,
    effectiveWeight: Math.round(effectiveWeight * 100) / 100,
    points: Math.round(((score * effectiveWeight) / 100) * 100) / 100,
    summary: "",
    reason: null,
    evidence: [],
  };
}

const unavailable: ReportCategory = {
  code: "security",
  label: "Безопасность",
  status: "unavailable",
  score: null,
  weight: 25,
  effectiveWeight: null,
  points: null,
  summary: "Результаты AppSec не получены.",
  reason: "appsec_not_available",
  evidence: [],
};

// Пример из отчёта gorod-dev/transit-api: безопасность без данных, измеренный вес — 75%.
const categories: ReportCategory[] = [
  unavailable,
  measured("cicd", 58, 20, 75),
  measured("documentation", 88, 20, 75),
  measured("activity", 91, 15, 75),
  measured("issues", 58, 15, 75),
  measured("code_health", 70, 5, 75),
];

describe("isMeasured", () => {
  it("не считает измеренной категорию без веса и баллов", () => {
    expect(isMeasured(unavailable)).toBe(false);
    expect(isMeasured(categories[1])).toBe(true);
  });
});

describe("pickHighlights", () => {
  it("делит оценённые категории на сильные и слабые, пропуская категории без данных", () => {
    const { strengths, weaknesses } = pickHighlights(categories);
    expect(strengths.map((category) => category.code)).toEqual(["activity", "documentation"]);
    expect(weaknesses.map((category) => category.code)).toEqual(["cicd", "issues"]);
  });

  it("берёт среднюю полосу, если низких оценок нет", () => {
    const { weaknesses } = pickHighlights([measured("a", 90, 50, 100), measured("b", 70, 50, 100)]);
    expect(weaknesses.map((category) => category.code)).toEqual(["b"]);
  });
});

describe("buildFormula", () => {
  it("собирает расчёт только из оценённых категорий", () => {
    expect(buildFormula(categories, 75, 73.4)).toBe("(58×20 + 88×20 + 91×15 + 58×15 + 70×5) ÷ 75 = 73,4");
  });

  it("не строит объяснение без измеренных категорий", () => {
    expect(buildFormula([unavailable], 0, 0)).toBeNull();
  });
});

describe("biggestLosses", () => {
  it("показывает, где теряется больше всего баллов", () => {
    const losses = biggestLosses(categories);
    expect(losses.map((loss) => loss.category.code)).toEqual(["cicd", "issues"]);
    expect(losses[0].lost).toBeCloseTo(11.2, 1);
    expect(losses[1].lost).toBeCloseTo(8.4, 1);
  });
});
