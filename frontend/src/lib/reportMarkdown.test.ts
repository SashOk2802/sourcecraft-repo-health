import { describe, expect, it } from "vitest";

import type { RepositoryReport } from "../api/report";
import { formatNumber, markdownFileName, renderReportMarkdown, reportFileBase } from "./reportMarkdown";

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
    id: "analysis-2026-09-15",
    status: "partial",
    analyzedAt: "2026-09-15T12:00:04Z",
    commitSha: "abc123",
    methodologyVersion: "v1",
    coverage: 0.75,
    isPreliminary: true,
    scoreLimit: null,
  },
  score: 73.4,
  scoreDetails: { measuredWeight: 75, applicableWeight: 100 },
  categories: [
    {
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
    },
    {
      code: "cicd",
      label: "CI/CD",
      status: "measured",
      score: 58,
      weight: 20,
      effectiveWeight: 26.67,
      points: 15.47,
      summary: "9 из 40 прогонов завершились неуспешно.",
      reason: null,
      evidence: [
        {
          code: "automated_ci_success_rate",
          value: 0.78,
          normalizedScore: 58,
          summary: "6 падений из 9 — на одном job.",
          evidence: [{ source: "sourcecraft-cicd", reference: "прогон #4812", summary: "таймаут", url: "https://x/ci/4812" }],
        },
      ],
    },
  ],
  recommendations: [
    {
      code: "cicd-flaky-job",
      priority: "p1",
      problem: "Нестабильный job.",
      action: "Разобраться с job e2e-tests",
      rationale: "Красный CI прячет ошибки.",
      expectedEffect: "CI/CD поднимется.",
      expectedScoreDelta: 5.9,
      evidence: [{ source: "sourcecraft-cicd", reference: "прогон #4807", summary: "таймаут", url: null }],
    },
  ],
};

describe("renderReportMarkdown", () => {
  const markdown = renderReportMarkdown(report);

  it("повторяет шапку и итог отчёта backend", () => {
    expect(markdown.startsWith("# Repo Health: team/platform-api\n\n")).toBe(true);
    expect(markdown).toContain("Анализ `analysis-2026-09-15` от 2026-09-15T12:00:04Z.");
    expect(markdown).toContain("**Repo Health Score: 73.4 / 100**");
    expect(markdown).toContain("Покрытие данных: 75 %.");
    expect(markdown).toContain("Предварительная оценка: да.");
  });

  it("категория без данных не участвует в расчёте и называет причину", () => {
    expect(markdown).toContain("- Статус: нет данных\n- Оценка: — / 100");
    expect(markdown).toContain("- Не участвует в расчёте.\n- Причина: `appsec_not_available`");
  });

  it("факты и ссылки — как в builder.py", () => {
    expect(markdown).toContain("- Факт `automated_ci_success_rate`: 6 падений из 9 — на одном job.");
    expect(markdown).toContain("- [sourcecraft-cicd: прогон #4812](https://x/ci/4812) — таймаут");
    expect(markdown).toContain("- sourcecraft-cicd: прогон #4807 — таймаут");
  });

  it("рекомендации с приоритетом и оговоркой про приросты", () => {
    expect(markdown).toContain("### P1: Разобраться с job e2e-tests");
    expect(markdown).toContain("Ожидаемое изменение Score: +5.9.");
    expect(markdown.trimEnd().endsWith("снять общее ограничение Score.")).toBe(true);
  });

  it("демо-отчёт помечен", () => {
    expect(renderReportMarkdown(report, { demo: true })).toContain("> Демо-отчёт по вымышленному репозиторию");
    expect(markdown).not.toContain("Демо-отчёт");
  });

  it("без рекомендаций так и пишет", () => {
    expect(renderReportMarkdown({ ...report, recommendations: [] })).toContain("## Рекомендации\n\nРекомендаций пока нет.");
  });
});

describe("formatNumber", () => {
  it("как _format_number в backend", () => {
    expect(formatNumber(100)).toBe("100");
    expect(formatNumber(73.4)).toBe("73.4");
    expect(formatNumber(26.666)).toBe("26.67");
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(null)).toBe("—");
  });
});

describe("markdownFileName", () => {
  it("строит имя из репозитория и даты анализа", () => {
    expect(markdownFileName(report)).toBe("repo-health-team-platform-api-2026-09-15.md");
  });

  it("у PDF то же имя: браузер берёт его из заголовка документа и добавляет .pdf", () => {
    expect(reportFileBase(report)).toBe("repo-health-team-platform-api-2026-09-15");
  });
});
