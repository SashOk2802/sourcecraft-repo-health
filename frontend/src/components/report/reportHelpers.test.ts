import { describe, expect, it } from "vitest";

import type { CategoryMetric, ReportCategory } from "../../api/report";
import {
  biggestLosses,
  buildFormula,
  hasMetrics,
  isMeasured,
  isTechnicalReference,
  metricEvidence,
  metricTone,
  splitHighlights,
  summarizeBands,
  visibleMetrics,
} from "./reportHelpers";

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

describe("metricTone", () => {
  const metric = (normalizedScore: number | null): CategoryMetric => ({
    code: "m",
    value: null,
    normalizedScore,
    summary: "",
    evidence: [],
  });

  it("берёт цвет из балла самой метрики", () => {
    expect(metricTone(metric(100))).toBe("high");
    expect(metricTone(metric(80))).toBe("high");
    expect(metricTone(metric(79))).toBe("mid");
    expect(metricTone(metric(60))).toBe("mid");
    expect(metricTone(metric(59))).toBe("low");
    expect(metricTone(metric(0))).toBe("low");
  });

  it("метрику без балла не красит: она справочная", () => {
    expect(metricTone(metric(null))).toBe("info");
  });
});

describe("hasMetrics", () => {
  it("пустой evidence прячет список", () => {
    expect(hasMetrics(unavailable)).toBe(false);
  });

  it("присланные метрики показываем", () => {
    const withMetric: ReportCategory = {
      ...unavailable,
      evidence: [{ code: "m", value: null, normalizedScore: 50, summary: "", evidence: [] }],
    };
    expect(hasMetrics(withMetric)).toBe(true);
  });
});

describe("summarizeBands", () => {
  it("раскладывает части проекта по полосам", () => {
    // Отчёт gorod-dev/transit-api: 58, 88, 91, 58, 70 и безопасность без данных.
    expect(summarizeBands(categories)).toEqual({ low: 2, mid: 1, high: 2, missing: 1 });
  });

  it("неприменимую категорию не считает ни в одной полосе", () => {
    const notApplicable: ReportCategory = { ...unavailable, status: "not_applicable", reason: null };
    expect(summarizeBands([notApplicable])).toEqual({ low: 0, mid: 0, high: 0, missing: 0 });
  });
});

describe("splitHighlights", () => {
  it("кладёт каждую измеренную часть ровно в одну колонку", () => {
    const { strengths, weaknesses, unchecked } = splitHighlights(categories);
    expect(strengths.map((category) => category.code)).toEqual(["activity", "documentation"]);
    // Сначала самые слабые; 70 — «стоит посмотреть», но сильной стороной не считается.
    expect(weaknesses.map((category) => category.code)).toEqual(["cicd", "issues", "code_health"]);
    expect(unchecked.map((category) => category.code)).toEqual(["security"]);
  });

  it("неприменимую категорию не выдаёт за непроверенную", () => {
    const notApplicable: ReportCategory = { ...unavailable, status: "not_applicable", reason: null };
    expect(splitHighlights([notApplicable])).toEqual({ strengths: [], weaknesses: [], unchecked: [] });
  });
});

// Так backend/app/analyzers/cicd.py отдаёт категорию без оценки: одна служебная метрика доступности.
const cicdUnavailable: ReportCategory = {
  code: "cicd",
  label: "CI/CD",
  status: "unavailable",
  score: null,
  weight: 20,
  effectiveWeight: null,
  points: null,
  summary: "Не удалось получить историю запусков CI/CD.",
  reason: "cicd_runs_unavailable",
  evidence: [
    {
      code: "cicd_data_availability",
      value: "unavailable",
      normalizedScore: null,
      summary: "Не удалось получить историю запусков CI/CD.",
      evidence: [
        { source: "sourcecraft-cicd", reference: "ci-runs", summary: "Не удалось получить историю запусков CI/CD.", url: null },
      ],
    },
  ],
};

describe("visibleMetrics", () => {
  it("у категории без оценки прячет служебную метрику доступности", () => {
    expect(visibleMetrics(cicdUnavailable)).toEqual([]);
    expect(hasMetrics(cicdUnavailable)).toBe(false);
  });

  it("у измеренной категории показывает все метрики", () => {
    const measuredCicd: ReportCategory = {
      ...measured("cicd", 58, 20, 75),
      evidence: [
        { code: "automated_ci_outcome_runs", value: 40, normalizedScore: null, summary: "С итогом: 40.", evidence: [] },
        { code: "automated_ci_success_rate", value: 57.5, normalizedScore: 57.5, summary: "Успешно 23 из 40.", evidence: [] },
      ],
    };
    expect(visibleMetrics(measuredCicd).map((metric) => metric.code)).toEqual([
      "automated_ci_outcome_runs",
      "automated_ci_success_rate",
    ]);
  });
});

describe("metricEvidence", () => {
  it("не повторяет текст метрики в её же факте без ссылки", () => {
    expect(metricEvidence(cicdUnavailable.evidence[0])).toEqual([]);
  });

  it("факты со ссылкой оставляет", () => {
    const metric: CategoryMetric = {
      code: "last_activity_days",
      value: 3,
      normalizedScore: 100,
      summary: "последняя активность 3 дн. назад",
      evidence: [{ source: "sourcecraft", reference: "last_updated", summary: "последняя активность 3 дн. назад", url: "https://x" }],
    };
    expect(metricEvidence(metric)).toHaveLength(1);
  });
});

describe("isTechnicalReference", () => {
  it("узнаёт служебные коды", () => {
    expect(isTechnicalReference("ci-runs")).toBe(true);
    expect(isTechnicalReference("last_updated")).toBe(true);
  });

  it("номера, теги, файлы и слова оставляет", () => {
    for (const reference of ["#311", "79", "v2.14.0", "README.md", "src/sync/importer.ts", "CODEOWNERS", "AppSec", "readme"]) {
      expect(isTechnicalReference(reference)).toBe(false);
    }
  });
});
