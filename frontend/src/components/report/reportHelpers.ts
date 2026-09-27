import type { Evidence } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
import { formatPoints, formatScore, formatShare } from "../../lib/format";
import { getScoreBand } from "../../lib/scoreBands";

/*
 * Здесь только подготовка к выводу того, что прислал backend: веса и вклад приходят
 * в процентах, новых оценок интерфейс не придумывает.
 */

export type MeasuredCategory = ReportCategory & {
  status: "measured";
  score: number;
  effectiveWeight: number;
  points: number;
};

export function isMeasured(category: ReportCategory): category is MeasuredCategory {
  return (
    category.status === "measured" &&
    category.score !== null &&
    category.effectiveWeight !== null &&
    category.points !== null
  );
}

export interface Loss {
  category: MeasuredCategory;
  /** Сколько баллов категория недобрала до своего максимума. */
  lost: number;
}

/** Категории, на которых теряется больше всего баллов (не меньше minLost). */
export function biggestLosses(categories: ReportCategory[], limit = 2, minLost = 3): Loss[] {
  return categories
    .filter(isMeasured)
    .map((category) => ({ category, lost: category.effectiveWeight - category.points }))
    .filter((loss) => loss.lost >= minLost)
    .sort((a, b) => b.lost - a.lost)
    .slice(0, limit);
}

/**
 * Строка расчёта из весов и оценок backend: «(58×20 + 88×20) ÷ 40 = 73,4».
 * Веса приходят в процентах методики, делитель — вес измеренных категорий.
 */
export function buildFormula(categories: ReportCategory[], measuredWeight: number, result: number): string | null {
  const measured = categories.filter(isMeasured);
  if (measured.length === 0 || measuredWeight <= 0) {
    return null;
  }
  const terms = measured.map((category) => `${formatPoints(category.score)}×${formatPoints(category.weight)}`);
  return `(${terms.join(" + ")}) ÷ ${formatPoints(measuredWeight)} = ${formatPoints(result)}`;
}

export type MetricTone = "high" | "mid" | "low" | "info";

/**
 * Цвет метрики берётся из её собственного normalizedScore по тем же полосам,
 * что и оценки категорий. null — справочная метрика, её не оценивают.
 */
export function metricTone(metric: CategoryMetric): MetricTone {
  return metric.normalizedScore === null ? "info" : getScoreBand(metric.normalizedScore);
}

/**
 * Какие метрики показать в карточке категории. У категории без оценки анализаторы
 * CI/CD и Security кладут служебную метрику доступности (cicd_data_availability,
 * appsec_data_availability), и её текст повторяет summary. Вместо неё карточка
 * показывает summary и понятную причину, а остальные метрики — если они что-то добавляют.
 */
export function visibleMetrics(category: ReportCategory): CategoryMetric[] {
  if (isMeasured(category)) {
    return category.evidence;
  }
  const summary = category.summary.trim();
  return category.evidence.filter(
    (metric) => !metric.code.endsWith("_data_availability") && metric.summary.trim() !== summary,
  );
}

/*
 * Признаки «есть/нет» анализатора документации (backend/app/analyzers/documentation.py):
 * value 1 или 0, оценка 100 или 0. Баллы «100» и «0» у них читаются как оценка, а summary
 * служебный («Наличие файла/информации: README.md») — поэтому подпись и значение свои.
 */
const presenceLabels: Record<string, string> = {
  has_readme: "README",
  has_contributing: "CONTRIBUTING — правила участия",
  has_license: "Лицензия",
  has_codeowners: "CODEOWNERS — ответственные за код",
  has_shortcuts: "Инструкция по запуску и тестам в README",
};

export function isPresenceMetric(metric: CategoryMetric): boolean {
  return metric.code in presenceLabels && (metric.value === 0 || metric.value === 1);
}

/*
 * Метрики Security Score v1 (backend PR #60, backend/app/analyzers/security.py). Их summary —
 * правила методики («Открытые findings учитываются по severity и статусу SourceCraft»), а не
 * подписи, а normalizedScore открытых находок повторяет оценку всей категории. Поэтому подпись
 * своя, справа — число находок, а цвет точки остаётся по оценке.
 */
const securityLabels: Record<string, string> = {
  appsec_data_coverage: "Результаты SAST, SCA и secret scanning",
  appsec_open_findings: "Открытые находки сканеров",
  appsec_confirmed_open_critical_findings: "Из них подтверждённые критичные",
};

/*
 * Доля файлов с пометками (backend PR #57: code_health.debt_file_ratio) — число от 0 до 1
 * без оценки, а summary backend с формулой в скобках. Подпись своя, значение — в процентах.
 */
const shareLabels: Record<string, string> = {
  "code_health.debt_file_ratio": "Доля файлов с TODO или FIXME",
};

/** Подпись метрики: у признака «есть/нет», метрик безопасности и долей — своя, у остальных — summary backend. */
export function metricLabel(metric: CategoryMetric): string {
  if (isPresenceMetric(metric)) {
    return presenceLabels[metric.code];
  }
  return securityLabels[metric.code] ?? shareLabels[metric.code] ?? metric.summary;
}

/**
 * Значение справа от метрики: её оценка 0–100; у признака — «есть» или «нет»; у справочной
 * метрики без оценки — само число, если оно есть: так Code health присылает, сколько
 * найдено TODO и FIXME и сколько файлов проверено. У находок безопасности — их число,
 * у доли — проценты.
 */
export function metricValueText(metric: CategoryMetric): string {
  if (isPresenceMetric(metric)) {
    return metric.value === 1 ? "есть" : "нет";
  }
  if (metric.code === "appsec_data_coverage") {
    return metric.value === "complete" ? "полные" : "—";
  }
  if (metric.code in shareLabels && typeof metric.value === "number" && Number.isFinite(metric.value)) {
    return formatShare(metric.value);
  }
  if (metric.normalizedScore !== null && !(metric.code in securityLabels)) {
    return formatScore(metric.normalizedScore);
  }
  if (typeof metric.value === "number" && Number.isFinite(metric.value)) {
    return formatPoints(metric.value);
  }
  return "—";
}

/**
 * Summary категории без повтора её оценки. Документация и Code health начинают его с того
 * же числа, что уже стоит рядом: «Оценка документации: 85/100. Проверены базовые файлы…».
 */
export function summaryWithoutScore(summary: string): string {
  return summary.replace(/^\s*Оценка[^:]{0,40}:\s*\d+(?:[.,]\d+)?\s*\/\s*100\s*\.?\s*/i, "").trim();
}

/**
 * Служебная ссылка факта вида ci-runs или last_updated: человеку она ничего не говорит,
 * поэтому вместо неё показываем описание. Номера задач, теги релизов и пути к файлам
 * остаются как есть.
 */
export function isTechnicalReference(reference: string): boolean {
  return /^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+$/.test(reference);
}

/** Полный SHA коммита в ссылке факта сокращаем, как в шапке отчёта: «коммит 0f3c9a1». */
export function evidenceReferenceText(reference: string): string {
  return /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/i.test(reference) ? `коммит ${reference.slice(0, 7)}` : reference;
}

/**
 * Описание факта без повтора ссылки. Code health пишет к пометке «TODO на строке 42 в файле
 * src/app.py.» при ссылке «src/app.py:42» — из описания остаётся только «TODO».
 */
export function evidenceSummaryText(item: Evidence): string {
  const marker = item.summary.match(/^(TODO|FIXME) на строке (\d+) в файле (.+?)\.?$/);
  return marker && item.reference === `${marker[3]}:${marker[2]}` ? marker[1] : item.summary;
}

/**
 * Факты метрики без повтора текста — её собственного или соседних метрик той же категории:
 * у служебных фактов описание совпадает с метрикой, а Security кладёт один и тот же факт
 * «Получены полные… результаты» во все свои метрики. Факты со ссылкой остаются всегда.
 */
export function metricEvidence(metric: CategoryMetric, siblings: CategoryMetric[] = []): Evidence[] {
  const repeated = new Set([metric, ...siblings].map((item) => item.summary.trim()));
  return metric.evidence.filter((item) => item.url !== null || !repeated.has(item.summary.trim()));
}

/**
 * Список метрик показываем, только если backend их прислал: при unavailable,
 * insufficient_sample и пока анализатор не отдал детализацию evidence пустой.
 */
export function hasMetrics(category: ReportCategory): boolean {
  return visibleMetrics(category).length > 0;
}

export interface BandSummary {
  /** Оценка ниже 60. */
  low: number;
  /** 60–79. */
  mid: number;
  /** 80 и выше. */
  high: number;
  /** Нет данных, мало данных или не удалось получить. «Не применимо» сюда не входит. */
  missing: number;
}

/**
 * Сколько частей проекта в каждой полосе — для сводки над радаром:
 * «2 проблемы · 1 стоит посмотреть · 2 хорошо». Неприменимые категории
 * не считаются нигде: к этому репозиторию они просто не относятся.
 */
export function summarizeBands(categories: ReportCategory[]): BandSummary {
  const summary: BandSummary = { low: 0, mid: 0, high: 0, missing: 0 };
  for (const category of categories) {
    if (category.status === "not_applicable") continue;
    if (isMeasured(category)) {
      summary[getScoreBand(category.score)] += 1;
    } else {
      summary.missing += 1;
    }
  }
  return summary;
}

export interface Highlights {
  /** 80 и выше — от лучшей к худшей. */
  strengths: MeasuredCategory[];
  /** Ниже 80 — сначала самые слабые. */
  weaknesses: MeasuredCategory[];
  /** Проверить не удалось. «Не применимо» сюда не входит: к репозиторию оно не относится. */
  unchecked: ReportCategory[];
}

/**
 * Сильные и слабые стороны (ТЗ, п. 3.4): каждая измеренная категория попадает
 * ровно в одну колонку, граница — та же полоса 80, что и в остальном отчёте.
 */
export function splitHighlights(categories: ReportCategory[]): Highlights {
  const measured = categories.filter(isMeasured);
  return {
    strengths: measured.filter((category) => getScoreBand(category.score) === "high").sort((a, b) => b.score - a.score),
    weaknesses: measured.filter((category) => getScoreBand(category.score) !== "high").sort((a, b) => a.score - b.score),
    unchecked: categories.filter((category) => category.status !== "not_applicable" && !isMeasured(category)),
  };
}
