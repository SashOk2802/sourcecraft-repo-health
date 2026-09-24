import type { Evidence } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
import { formatPoints } from "../../lib/format";
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

/**
 * Служебная ссылка факта вида ci-runs или last_updated: человеку она ничего не говорит,
 * поэтому вместо неё показываем описание. Номера задач, теги релизов и пути к файлам
 * остаются как есть.
 */
export function isTechnicalReference(reference: string): boolean {
  return /^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+$/.test(reference);
}

/** Факты метрики без повтора её же текста: у служебных фактов описание совпадает с метрикой. */
export function metricEvidence(metric: CategoryMetric): Evidence[] {
  const summary = metric.summary.trim();
  return metric.evidence.filter((item) => item.url !== null || item.summary.trim() !== summary);
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
