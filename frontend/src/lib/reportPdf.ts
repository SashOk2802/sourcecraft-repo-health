import type { Evidence, RecommendationPriority } from "../api/common";
import type { Recommendation, ReportCategory, RepositoryReport } from "../api/report";
import { buildFormula } from "../components/report/reportHelpers";
import { formatDateTime, formatPoints, formatScore, formatShare } from "./format";
import { categoryStatusLabels, priorityLabels } from "./labels";
import { getScoreBand, type ScoreBand } from "./scoreBands";
import { scoreVerdict } from "./verdict";

/*
 * PDF-отчёт собирается в браузере через pdfmake: настоящий файл с текстом, который можно
 * выделить и найти поиском, а не окно печати. Здесь только описание документа — данные
 * те же, что на странице отчёта и в Markdown-выгрузке. Сам pdfmake грузится при нажатии
 * «Скачать PDF» (reportPdfRender.ts) и в общий бандл не попадает.
 */

/** Документ pdfmake: только те поля, которые мы используем. */
export interface PdfDocument {
  pageSize: "A4";
  pageMargins: [number, number, number, number];
  info: { title: string; subject: string; creator: string };
  defaultStyle: Record<string, unknown>;
  styles: Record<string, Record<string, unknown>>;
  content: PdfNode[];
  footer: (currentPage: number, pageCount: number) => PdfNode;
}

export type PdfNode = string | { [key: string]: unknown };

// Цвета светлой темы интерфейса (tokens.css): отчёт на бумаге всегда светлый.
const colors = {
  text: "#14152b",
  secondary: "#5f6072",
  hint: "#9a9baa",
  accent: "#4e79eb",
  line: "#e3e4ea",
  soft: "#f4f5f8",
  band: { high: "#2b9e5f", mid: "#dd9613", low: "#e5442f" } satisfies Record<ScoreBand, string>,
};

const priorityColors: Record<RecommendationPriority, string> = {
  p0: colors.band.low,
  p1: colors.band.mid,
  p2: colors.accent,
  p3: colors.hint,
};

/** Тонкие горизонтальные линии между строками таблицы, как в интерфейсе. */
const tableLayout = {
  hLineWidth: (index: number) => (index === 0 ? 0 : 0.5),
  vLineWidth: () => 0,
  hLineColor: () => colors.line,
  paddingLeft: (index: number) => (index === 0 ? 0 : 6),
  paddingRight: () => 6,
  paddingTop: () => 5,
  paddingBottom: () => 5,
};

export interface ReportPdfOptions {
  /** Демо-отчёт по вымышленному репозиторию — помечаем, как на странице. */
  demo?: boolean;
  /** Адрес сервиса для ссылки на методику: window.location.origin. */
  siteUrl?: string;
}

export function buildReportPdf(report: RepositoryReport, { demo = false, siteUrl }: ReportPdfOptions = {}): PdfDocument {
  const { repository, analysis, categories, recommendations, score } = report;
  const verdict = scoreVerdict(score, analysis.isPreliminary);
  const formula = score === null ? null : buildFormula(categories, report.scoreDetails.measuredWeight, score);

  const content: PdfNode[] = [
    { text: "Repo Health · отчёт о здоровье репозитория", style: "kicker" },
    {
      text: [{ text: `${repository.organizationSlug} / `, color: colors.secondary }, repository.repositorySlug],
      style: "title",
    },
    {
      text: [
        `Анализ от ${formatDateTime(analysis.analyzedAt)}`,
        analysis.commitSha ? ` · коммит ${analysis.commitSha.slice(0, 7)}` : "",
        ` · методика ${analysis.methodologyVersion} · ${analysis.id}`,
      ].join(""),
      style: "meta",
    },
  ];
  if (repository.url) {
    content.push({ text: repository.url, link: repository.url, style: "link", margin: [0, 2, 0, 0] });
  }
  if (demo) {
    content.push({ text: "Демо-отчёт по вымышленному репозиторию — так выглядит выгрузка настоящего анализа.", style: "note" });
  }

  content.push({
    columns: [
      {
        width: "auto",
        text: score === null ? "—" : formatScore(score),
        style: "score",
        color: score === null ? colors.hint : colors.band[getScoreBand(score)],
      },
      {
        width: "*",
        margin: [16, 6, 0, 0],
        stack: compact([
          { text: verdict.title, style: "verdict" },
          { text: `${verdict.note} Шкала — от 0 до 100.`, style: "muted" },
          {
            text:
              `Полнота данных: ${analysis.coverage === null ? "—" : formatShare(analysis.coverage)} · ` +
              `измерено ${formatPoints(report.scoreDetails.measuredWeight)}% из ` +
              `${formatPoints(report.scoreDetails.applicableWeight)}% веса`,
            style: "muted",
          },
          formula && { text: `Как посчитали: ${formula}`, style: "muted" },
        ]),
      },
    ],
    margin: [0, 14, 0, 4],
  });

  if (analysis.scoreLimit) {
    content.push({
      text:
        `${analysis.scoreLimit.summary} Без ограничения Score был бы ${formatPoints(analysis.scoreLimit.uncappedScore)}, ` +
        `сейчас он не выше ${formatPoints(analysis.scoreLimit.value)}.`,
      style: "warning",
    });
  }

  content.push({ text: "Категории", style: "h2" }, categoriesTable(categories));

  content.push({ text: "Что сделать", style: "h2" });
  if (recommendations.length === 0) {
    content.push({
      text:
        score === null
          ? "Рекомендаций пока нет: без данных не на что опереться."
          : "Рекомендаций нет: по собранным данным срочно исправлять нечего.",
      style: "muted",
    });
  } else {
    recommendations.forEach((recommendation, index) => content.push(recommendationBlock(recommendation, index)));
    content.push({
      text: "Приросты не суммируются: рекомендации могут влиять на одни и те же метрики или снимать общее ограничение Score.",
      style: "muted",
      margin: [0, 8, 0, 0],
    });
  }

  const withFacts = categories.filter((category) => category.evidence.length > 0);
  if (withFacts.length > 0) {
    content.push({ text: "Факты по категориям", style: "h2" });
    for (const category of withFacts) content.push(...categoryFacts(category));
  }

  content.push({
    text: compact([
      "Оценка строится только на данных SourceCraft: истории Git, CI, issues, merge requests и AppSec. ",
      "«Нет данных» не равно нулю: такая категория не участвует в расчёте. ",
      siteUrl ? { text: "Как считаем", link: `${siteUrl}/methodology`, color: colors.accent } : "Методика — «Как считаем» на сайте сервиса",
      ".",
    ]),
    style: "muted",
    margin: [0, 18, 0, 0],
  });

  return {
    pageSize: "A4",
    pageMargins: [40, 40, 40, 48],
    info: {
      title: `Repo Health: ${repository.name}`,
      subject: "Отчёт о здоровье репозитория SourceCraft",
      creator: "Repo Health",
    },
    defaultStyle: { font: "Roboto", fontSize: 10, lineHeight: 1.25, color: colors.text },
    styles: {
      kicker: { fontSize: 9, bold: true, color: colors.accent },
      title: { fontSize: 20, bold: true, margin: [0, 4, 0, 4] },
      meta: { fontSize: 9, color: colors.secondary },
      link: { fontSize: 9, color: colors.accent },
      note: { fontSize: 9, color: colors.secondary, italics: true, margin: [0, 6, 0, 0] },
      score: { fontSize: 44, bold: true },
      verdict: { fontSize: 13, bold: true, margin: [0, 0, 0, 2] },
      muted: { fontSize: 9, color: colors.secondary },
      warning: { fontSize: 9, color: colors.band.low, margin: [0, 4, 0, 0] },
      h2: { fontSize: 14, bold: true, margin: [0, 18, 0, 6] },
      h3: { fontSize: 11, bold: true, margin: [0, 10, 0, 3] },
      th: { fontSize: 8, color: colors.secondary },
      aiLabel: { fontSize: 8, bold: true, color: colors.secondary },
    },
    content,
    footer: (currentPage, pageCount) => ({
      columns: [
        { text: `Repo Health · ${repository.name}`, color: colors.hint },
        { text: `${currentPage} из ${pageCount}`, color: colors.hint, alignment: "right" },
      ],
      fontSize: 8,
      margin: [40, 16, 40, 0],
    }),
  };
}

function categoriesTable(categories: ReportCategory[]): PdfNode {
  const header = ["Категория", "Вес", "Оценка", "Что видно"].map((text) => ({ text, style: "th" }));
  const rows = categories.map((category) => [
    { text: category.label, bold: true },
    { text: `${formatPoints(category.weight)}%`, color: colors.secondary },
    category.score === null
      ? { text: categoryStatusLabels[category.status], color: colors.secondary }
      : { text: formatScore(category.score), bold: true, color: colors.band[getScoreBand(category.score)] },
    {
      text:
        category.effectiveWeight === null && category.status !== "not_applicable"
          ? `${category.summary} Не участвует в расчёте.`
          : category.summary,
    },
  ]);
  return { table: { headerRows: 1, widths: [96, 34, 70, "*"], body: [header, ...rows] }, layout: tableLayout };
}

function recommendationBlock(recommendation: Recommendation, index: number): PdfNode {
  const delta = recommendation.expectedScoreDelta;
  return {
    stack: compact([
      {
        text: [
          { text: `${index + 1}. ${priorityLabels[recommendation.priority]}  `, color: priorityColors[recommendation.priority], bold: true },
          { text: recommendation.action, bold: true },
        ],
        margin: [0, 10, 0, 2],
      },
      { text: recommendation.problem },
      { text: [{ text: "Почему важно: ", color: colors.secondary }, recommendation.rationale] },
      recommendation.expectedEffect && {
        text: [{ text: "Что изменится: ", color: colors.secondary }, recommendation.expectedEffect],
      },
      delta !== null &&
        delta > 0 && {
          text: [{ text: "Ожидаемый прирост: ", color: colors.secondary }, `+${formatPoints(delta)}`],
        },
      recommendation.evidence.length > 0 && evidenceList(recommendation.evidence),
      recommendation.aiActionPlan && {
        table: {
          widths: ["*"],
          body: [
            [
              {
                stack: [
                  { text: "AI-ПЛАН ДЕЙСТВИЙ · сгенерирован по фактам анализа", style: "aiLabel" },
                  { text: recommendation.aiActionPlan, margin: [0, 2, 0, 0] },
                ],
                fillColor: colors.soft,
              },
            ],
          ],
        },
        layout: {
          hLineWidth: () => 0,
          vLineWidth: (index: number) => (index === 0 ? 2 : 0),
          vLineColor: () => colors.line,
          paddingLeft: () => 10,
          paddingRight: () => 10,
          paddingTop: () => 6,
          paddingBottom: () => 6,
        },
        margin: [0, 6, 0, 0],
      },
    ]),
  };
}

function categoryFacts(category: ReportCategory): PdfNode[] {
  const status =
    category.score === null ? categoryStatusLabels[category.status] : `оценка ${formatScore(category.score)}`;
  return [
    { text: [category.label, { text: ` · ${status}`, color: colors.secondary, bold: false }], style: "h3" },
    {
      ul: category.evidence.map((metric) => ({
        stack: compact([
          {
            text: compact([
              metric.summary,
              metric.normalizedScore !== null && { text: ` — ${formatScore(metric.normalizedScore)}`, color: colors.secondary },
            ]),
          },
          metric.evidence.length > 0 && evidenceList(metric.evidence),
        ]),
      })),
      margin: [0, 0, 0, 2],
    },
  ];
}

/** Ссылки на факты SourceCraft: кликабельные в PDF, с пояснением, как на странице. */
function evidenceList(items: Evidence[]): PdfNode {
  return {
    ul: items.map((evidence) => ({
      text: [
        evidence.url
          ? { text: `${evidence.source}: ${evidence.reference}`, link: evidence.url, color: colors.accent }
          : `${evidence.source}: ${evidence.reference}`,
        evidence.summary ? ` — ${evidence.summary}` : "",
      ],
    })),
    fontSize: 9,
    color: colors.secondary,
    margin: [0, 2, 0, 0],
  };
}

/** Убирает пустые элементы — удобно для необязательных строк документа. */
function compact<T>(items: Array<T | false | null | undefined | "">): T[] {
  return items.filter((item): item is T => Boolean(item));
}
