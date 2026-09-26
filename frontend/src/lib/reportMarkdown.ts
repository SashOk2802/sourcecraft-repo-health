import type { CategoryStatus, Evidence } from "../api/common";
import type { RepositoryReport } from "../api/report";

/*
 * Markdown-отчёт в том же формате, что render_markdown_report в
 * backend/app/reporting/builder.py. Настоящий отчёт отдаёт backend
 * (GET /api/v1/analyses/{id}/report.md); здесь собираются только демо-отчёты,
 * чтобы выгрузка на стенде работала и без него. Меняется формат там — меняем и здесь.
 */

const statusLabels: Record<CategoryStatus, string> = {
  measured: "рассчитано",
  unavailable: "нет данных",
  not_applicable: "неприменимо",
  insufficient_sample: "недостаточно данных",
  error: "ошибка анализа",
};

export function renderReportMarkdown(report: RepositoryReport, { demo = false }: { demo?: boolean } = {}): string {
  const { repository, analysis, categories, recommendations } = report;

  const lines = [`# Repo Health: ${repository.name}`, ""];
  if (demo) {
    lines.push("> Демо-отчёт по вымышленному репозиторию: так выглядит выгрузка настоящего анализа.", "");
  }
  lines.push(
    `Анализ \`${analysis.id}\` от ${analysis.analyzedAt}.`,
    `Коммит: \`${analysis.commitSha ?? "—"}\`. Методика: ${analysis.methodologyVersion}.`,
    "",
    "## Итоговая оценка",
    "",
    `**Repo Health Score: ${formatNumber(report.score)} / 100**`,
    `Покрытие данных: ${formatPercentage(analysis.coverage)}.`,
    `Предварительная оценка: ${analysis.isPreliminary ? "да" : "нет"}.`,
  );

  if (analysis.scoreLimit) {
    lines.push(
      "",
      "### Ограничение Score",
      "",
      analysis.scoreLimit.summary,
      `Без ограничения: ${formatNumber(analysis.scoreLimit.uncappedScore)}; ` +
        `максимальный Score: ${formatNumber(analysis.scoreLimit.value)}.`,
    );
  }

  lines.push("", "## Категории");
  for (const category of categories) {
    lines.push(
      "",
      `### ${category.label}`,
      "",
      `- Статус: ${statusLabels[category.status]}`,
      `- Оценка: ${formatNumber(category.score)} / 100`,
      `- Базовый вес: ${formatNumber(category.weight)} %`,
      `- Фактический вес: ${formatNumber(category.effectiveWeight)} %`,
      `- Вклад в Score: ${formatNumber(category.points)}`,
      `- ${category.summary}`,
    );
    if (category.effectiveWeight === null) {
      lines.push("- Не участвует в расчёте.");
    }
    if (category.reason) {
      lines.push(`- Причина: \`${category.reason}\``);
    }
    for (const fact of category.evidence) {
      lines.push(`- Факт \`${fact.code}\`: ${fact.summary}`);
      for (const evidence of fact.evidence) {
        lines.push(evidenceMarkdown(evidence));
      }
    }
  }

  lines.push("", "## Рекомендации");
  if (recommendations.length === 0) {
    lines.push("", "Рекомендаций пока нет.");
  } else {
    for (const recommendation of recommendations) {
      lines.push(
        "",
        `### ${recommendation.priority.toUpperCase()}: ${recommendation.action}`,
        "",
        `Проблема: ${recommendation.problem}`,
        `Почему: ${recommendation.rationale}`,
      );
      if (recommendation.expectedEffect) {
        lines.push(`Ожидаемый эффект: ${recommendation.expectedEffect}`);
      }
      if (recommendation.expectedScoreDelta !== null) {
        lines.push(`Ожидаемое изменение Score: +${formatNumber(recommendation.expectedScoreDelta)}.`);
      }
      for (const evidence of recommendation.evidence) {
        lines.push(evidenceMarkdown(evidence));
      }
    }
    lines.push(
      "",
      "Возможные приросты не суммируются: рекомендации могут влиять на одни и те же " +
        "метрики или снять общее ограничение Score.",
    );
  }

  return `${lines.join("\n")}\n`;
}

/** Как _format_number в builder.py: два знака, без хвостовых нулей, точка как разделитель. */
export function formatNumber(value: number | null): string {
  if (value === null) return "—";
  return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function formatPercentage(value: number | null): string {
  return value === null ? "—" : `${formatNumber(value * 100)} %`;
}

function evidenceMarkdown(evidence: Evidence): string {
  const label = `${evidence.source}: ${evidence.reference}`;
  return evidence.url ? `- [${label}](${evidence.url}) — ${evidence.summary}` : `- ${label} — ${evidence.summary}`;
}

/** Имя файла выгрузки: repo-health-org-repo-2026-09-24.md. */
export function markdownFileName(report: RepositoryReport): string {
  const date = report.analysis.analyzedAt.slice(0, 10);
  const slug = `${report.repository.organizationSlug}-${report.repository.repositorySlug}`
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `repo-health-${slug || "report"}-${date}.md`;
}
