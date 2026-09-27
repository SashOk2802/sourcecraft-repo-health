import { describe, expect, it } from "vitest";

import type { Evidence } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
import {
  biggestLosses,
  buildFormula,
  evidenceReferenceText,
  evidenceSummaryText,
  hasMetrics,
  isMeasured,
  isPresenceMetric,
  isTechnicalReference,
  metricEvidence,
  metricLabel,
  metricTone,
  metricValueText,
  splitHighlights,
  summarizeBands,
  summaryWithoutScore,
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

// Так метрики отдают backend/app/analyzers/documentation.py и code_health.py.
const hasReadme: CategoryMetric = {
  code: "has_readme",
  value: 1,
  normalizedScore: 100,
  summary: "Наличие файла/информации: README.md",
  evidence: [],
};
const noCodeowners: CategoryMetric = { ...hasReadme, code: "has_codeowners", value: 0, normalizedScore: 0 };
const todoCount: CategoryMetric = {
  code: "todo_count",
  value: 1284,
  normalizedScore: null,
  summary: "Количество меток TODO в коде",
  evidence: [],
};

describe("метрики документации и Code health", () => {
  it("признак «есть/нет» показывает словом, а не баллами 100 и 0", () => {
    expect(isPresenceMetric(hasReadme)).toBe(true);
    expect(metricValueText(hasReadme)).toBe("есть");
    expect(metricValueText(noCodeowners)).toBe("нет");
    expect(metricLabel(noCodeowners)).toContain("CODEOWNERS");
    expect(metricLabel(noCodeowners)).not.toContain("Наличие");
    // Цвет остаётся по оценке: отсутствующий файл — красная точка.
    expect(metricTone(noCodeowners)).toBe("low");
  });

  it("у справочной метрики без оценки показывает число", () => {
    expect(isPresenceMetric(todoCount)).toBe(false);
    expect(metricLabel(todoCount)).toBe("Количество меток TODO в коде");
    expect(metricValueText(todoCount)).toBe("1 284");
    expect(metricValueText({ ...todoCount, value: "unavailable" })).toBe("—");
  });

  it("у метрики с оценкой показывает оценку", () => {
    expect(metricValueText({ ...todoCount, value: 0.3, normalizedScore: 44.4 })).toBe("44");
  });

  it("незнакомый has_* с другим значением считает обычной метрикой", () => {
    expect(isPresenceMetric({ ...hasReadme, code: "has_something", value: 1 })).toBe(false);
    expect(isPresenceMetric({ ...hasReadme, value: "да" })).toBe(false);
  });

  it("долю файлов с пометками показывает в процентах и без формулы в подписи", () => {
    // Так её отдаёт backend/app/analyzers/code_health.py: доля от 0 до 1, summary с формулой.
    const debtShare: CategoryMetric = {
      code: "code_health.debt_file_ratio",
      value: 9 / 120,
      normalizedScore: null,
      summary: "Доля файлов с техническим долгом (files_with_debt / total_files)",
      evidence: [],
    };
    expect(metricLabel(debtShare)).toBe("Доля файлов с TODO или FIXME");
    expect(metricValueText(debtShare)).toBe("8%");
    expect(metricTone(debtShare)).toBe("info");
  });

  it("возраст пометок объясняет без «shallow» и «blame»", () => {
    const markerAge: CategoryMetric = {
      code: "code_health.marker_age",
      value: null,
      normalizedScore: null,
      summary: "Возраст TODO/FIXME недоступен: клон shallow, истории для blame нет.",
      evidence: [],
    };
    expect(metricLabel(markerAge)).not.toMatch(/shallow|blame/);
    expect(metricValueText(markerAge)).toBe("—");
    expect(metricTone(markerAge)).toBe("info");
  });

  it("прочитанный до лимита объём показывает в мегабайтах", () => {
    const bytesRead: CategoryMetric = {
      code: "partial_bytes_read",
      value: 50 * 1024 * 1024,
      normalizedScore: null,
      summary: "Байт исходников прочитано до достижения лимита",
      evidence: [],
    };
    expect(metricValueText(bytesRead)).toBe("50 МБ");
  });
});

describe("подписи метрик Activity", () => {
  it("summary со строчной буквы показывает с заглавной", () => {
    // backend/app/analyzers/activity.py, метрика v2.
    const activeWeeks: CategoryMetric = {
      code: "active_weeks_in_period",
      value: 5,
      normalizedScore: 62.5,
      summary: "за период коммиты были в 5 неделях",
      evidence: [],
    };
    expect(metricLabel(activeWeeks)).toBe("За период коммиты были в 5 неделях");
    expect(metricValueText(activeWeeks)).toBe("63");
  });

  it("ссылку с тем же текстом, что у метрики, подписывает по-своему", () => {
    const summary = "за период коммиты были в 5 неделях";
    const history: Evidence = {
      source: "sourcecraft-activity",
      reference: "commit-history",
      summary,
      url: "https://sourcecraft.dev/team/api",
    };
    const activeWeeks: CategoryMetric = {
      code: "active_weeks_in_period",
      value: 5,
      normalizedScore: 62.5,
      summary,
      evidence: [history],
    };
    expect(metricEvidence(activeWeeks)).toEqual([{ ...history, summary: "история коммитов" }]);
    // Ссылка на релиз с повтором текста остаётся ссылкой, но без повтора.
    const release: Evidence = { ...history, reference: "v2.14.0" };
    expect(metricEvidence({ ...activeWeeks, evidence: [release] })).toEqual([{ ...release, summary: "" }]);
  });
});

// Так backend/app/analyzers/security.py отдаёт измеренную безопасность: три метрики и один общий факт.
const appsecFact: Evidence = {
  source: "sourcecraft-appsec",
  reference: "appsec-defects",
  summary: "Получены полные обезличенные результаты SAST, SCA и secret scanning.",
  url: null,
};
const appsecCoverage: CategoryMetric = {
  code: "appsec_data_coverage",
  value: "complete",
  normalizedScore: null,
  summary: appsecFact.summary,
  evidence: [appsecFact],
};
const appsecOpen: CategoryMetric = {
  code: "appsec_open_findings",
  value: 3,
  normalizedScore: 85,
  summary: "Открытые findings учитываются по severity и статусу SourceCraft.",
  evidence: [appsecFact],
};
const appsecCritical: CategoryMetric = {
  code: "appsec_confirmed_open_critical_findings",
  value: 0,
  normalizedScore: null,
  summary: "Критичные finding'и с подтверждённым открытым статусом ограничивают итоговый Score.",
  evidence: [appsecFact],
};

describe("метрики Security Score", () => {
  const securityMetrics = [appsecCoverage, appsecOpen, appsecCritical];

  it("подписывает своими словами, а не правилом методики", () => {
    expect(securityMetrics.map(metricLabel)).toEqual([
      "Результаты SAST, SCA и secret scanning",
      "Открытые находки сканеров",
      "Из них подтверждённые критичные",
    ]);
  });

  it("справа — число находок, а не повтор оценки категории", () => {
    expect(metricValueText(appsecCoverage)).toBe("полные");
    expect(metricValueText({ ...appsecCoverage, value: "partial" })).toBe("—");
    expect(metricValueText(appsecOpen)).toBe("3");
    expect(metricValueText(appsecCritical)).toBe("0");
    // Цвет точки — по оценке: 85 — хорошо; у подтверждённых критичных оценки нет.
    expect(metricTone(appsecOpen)).toBe("high");
    expect(metricTone(appsecCritical)).toBe("info");
  });

  it("общий факт не повторяет под каждой метрикой", () => {
    for (const metric of securityMetrics) {
      expect(metricEvidence(metric, securityMetrics)).toEqual([]);
    }
    // Без соседей факт остался бы: его текст отличается от summary самой метрики.
    expect(metricEvidence(appsecOpen)).toHaveLength(1);
  });
});

describe("summaryWithoutScore", () => {
  it("убирает повтор оценки в начале summary", () => {
    expect(summaryWithoutScore("Оценка документации: 85/100. Проверены базовые файлы репозитория.")).toBe(
      "Проверены базовые файлы репозитория.",
    );
    expect(summaryWithoutScore("Оценка чистоты кода: 69.4/100. Обнаружено TODO: 40, FIXME: 7.")).toBe(
      "Обнаружено TODO: 40, FIXME: 7.",
    );
    expect(summaryWithoutScore("Оценка документации: 85/100.")).toBe("");
  });

  it("обычный summary не трогает", () => {
    const summary = "Успешно прошли 23 из 40 автоматических прогонов CI за полгода.";
    expect(summaryWithoutScore(summary)).toBe(summary);
  });
});

describe("ссылки фактов", () => {
  it("полный SHA сокращает до коммита", () => {
    expect(evidenceReferenceText("0f3c9a1e".padEnd(40, "0"))).toBe("коммит 0f3c9a1");
    expect(evidenceReferenceText("#311")).toBe("#311");
    expect(evidenceReferenceText("d7bebd1")).toBe("d7bebd1");
  });

  it("не повторяет путь и строку пометки в описании", () => {
    const todo = { source: "git_repository", reference: "src/app.py:42", summary: "TODO на строке 42 в файле src/app.py.", url: null };
    expect(evidenceSummaryText(todo)).toBe("TODO");
    expect(evidenceSummaryText({ ...todo, reference: "src/other.py:42" })).toBe(todo.summary);
    expect(evidenceSummaryText({ ...todo, summary: "142 дня без движения" })).toBe("142 дня без движения");
  });
});
