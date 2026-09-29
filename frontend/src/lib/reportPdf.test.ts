import { describe, expect, it } from "vitest";

import type { RepositoryReport } from "../api/report";
import { buildReportPdf } from "./reportPdf";

const report: RepositoryReport = {
  repository: {
    id: "repo-42",
    organizationSlug: "team",
    repositorySlug: "platform-api",
    name: "team/platform-api",
    url: "https://sourcecraft.dev/team/platform-api",
  },
  badgeAvailable: true,
  analysis: {
    id: "analysis-7",
    status: "partial",
    analyzedAt: "2026-09-29T12:00:04Z",
    commitSha: "411effe0c1",
    methodologyVersion: "v2",
    coverage: 0.41,
    isPreliminary: true,
    scoreLimit: null,
  },
  score: 84.3,
  scoreDetails: { measuredWeight: 35, applicableWeight: 85 },
  categories: [
    {
      code: "cicd",
      label: "CI/CD",
      status: "measured",
      score: 90,
      weight: 20,
      effectiveWeight: 57.1,
      points: 51.4,
      summary: "CI стабильный.",
      reason: null,
      evidence: [
        {
          code: "automated_ci_success_rate",
          value: 0.9,
          normalizedScore: 90,
          summary: "Успешно 9 из 10 запусков.",
          evidence: [
            { source: "SourceCraft CI/CD", reference: "ci-run-4821", summary: "ошибка", url: "https://sourcecraft.dev/team/platform-api/cicd/runs/4821" },
          ],
        },
      ],
    },
    {
      code: "security",
      label: "Безопасность",
      status: "unavailable",
      score: null,
      weight: 25,
      effectiveWeight: null,
      points: null,
      summary: "Результаты AppSec не получены.",
      reason: "appsec_unavailable",
      evidence: [],
    },
  ],
  recommendations: [
    {
      code: "doc_missing_has_readme",
      priority: "p1",
      problem: "В репозитории проекта отсутствует README.md.",
      action: "Добавьте README.md с описанием проекта.",
      rationale: "Документация упрощает онбординг.",
      expectedEffect: null,
      expectedScoreDelta: 20,
      evidence: [],
      aiActionPlan: "1. Создайте README.md.\n2. Опишите запуск.",
    },
  ],
};

const flatten = (node: unknown): string => JSON.stringify(node);

describe("buildReportPdf", () => {
  it("описывает отчёт: репозиторий, оценку, категории и рекомендации", () => {
    const document = buildReportPdf(report, { siteUrl: "https://alhamdulylia.ru" });
    const text = flatten(document.content);

    expect(document.info.title).toBe("Repo Health: team/platform-api");
    expect(text).toContain("platform-api");
    expect(text).toContain('"84"');
    expect(text).toContain("Оценка предварительная");
    expect(text).toContain("CI/CD");
    // Без данных — не ноль, а статус.
    expect(text).toContain("нет данных");
    expect(text).toContain("Не участвует в расчёте.");
    expect(text).toContain("Добавьте README.md с описанием проекта.");
    expect(text).toContain("+20");
    expect(text).toContain("https://alhamdulylia.ru/methodology");
  });

  it("ссылки на факты кликабельные, AI-план помечен как сгенерированный", () => {
    const text = flatten(buildReportPdf(report).content);
    expect(text).toContain('"link":"https://sourcecraft.dev/team/platform-api/cicd/runs/4821"');
    expect(text).toContain("AI-ПЛАН ДЕЙСТВИЙ");
    expect(text).toContain("1. Создайте README.md.");
  });

  it("помечает демо и нумерует страницы", () => {
    const document = buildReportPdf(report, { demo: true });
    expect(flatten(document.content)).toContain("Демо-отчёт");
    expect(flatten(document.footer(2, 5))).toContain("2 из 5");
  });
});
