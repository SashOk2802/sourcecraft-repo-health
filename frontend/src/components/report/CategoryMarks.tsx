import { Text } from "@gravity-ui/uikit";
import type { CSSProperties } from "react";

import type { CategoryStatus } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
import { describeCategory } from "../../lib/categoryMeaning";
import { cn } from "../../lib/classNames";
import { formatPoints, formatScore } from "../../lib/format";
import { describeReason } from "../../lib/reasonCodes";
import { getScoreBand, SCORE_BAND_LIMITS } from "../../lib/scoreBands";
import { CategoryStatusLabel } from "../StatusLabels";
import { EvidenceLinks } from "./EvidenceLinks";
import { hasMetrics, metricTone } from "./reportHelpers";
import "./CategoryMarks.css";

/*
 * Карточка на категорию: внутри её метрики из evidence.
 * Цвет метрики — по её собственному normalizedScore, а не по «плюс/минус»:
 * такого признака backend не присылает. Пустой evidence — показываем
 * summary и причину вместо списка.
 */
export function CategoryMarks({ categories }: { categories: ReportCategory[] }) {
  return (
    <section className="section">
      <div className="section__head">
        <Text variant="subheader-2" as="h2">
          Из чего складывается
        </Text>
        <Text variant="body-1" color="secondary">
          каждая часть проверяется по нескольким признакам: {SCORE_BAND_LIMITS.high} и выше — хорошо,
          {SCORE_BAND_LIMITS.low}–{SCORE_BAND_LIMITS.high - 1} — стоит посмотреть, ниже {SCORE_BAND_LIMITS.low} —
          проблема; серая точка — признак для справки, его не оценивают
        </Text>
      </div>

      <div className="category-marks">
        {categories.map((category, index) => (
          <CategoryCard key={category.code} category={category} order={index} />
        ))}
      </div>
    </section>
  );
}

function CategoryCard({ category, order }: { category: ReportCategory; order: number }) {
  const score = category.status === "measured" ? category.score : null;
  const band = score !== null ? getScoreBand(score) : null;
  const meaning = describeCategory(category.code);

  return (
    <article className="category-card" style={{ "--rh-step": order } as CSSProperties}>
      <header className="category-card__head">
        <div className="category-card__title">
          <Text variant="subheader-1" as="h3">
            {category.label}
          </Text>
          {meaning && (
            <Text variant="body-1" color="hint" className="category-card__meaning">
              {meaning}
            </Text>
          )}
        </div>
        {score !== null && (
          <span className={cn("category-card__score", band && `category-card__score_band_${band}`)}>
            <span className="num">{formatScore(score)}</span>
          </span>
        )}
      </header>

      <div className="category-card__status">
        {score === null && <CategoryStatusLabel status={category.status as Exclude<CategoryStatus, "measured">} />}
        <Text variant="body-1" color="hint">
          {category.effectiveWeight === null
            ? "в оценку не вошло — её доля перешла к остальным частям"
            : `влияет на оценку на ${formatPoints(category.effectiveWeight)}%`}
        </Text>
      </div>

      {hasMetrics(category) ? (
        <ul className="category-card__metrics">
          {category.evidence.map((metric) => (
            <MetricRow key={metric.code} metric={metric} />
          ))}
        </ul>
      ) : (
        <p className="category-card__reason">
          <Text variant="body-2">{category.summary}</Text>{" "}
          {category.reason !== null && (
            <Text variant="body-2" color="secondary">
              {describeReason(category.reason)}
            </Text>
          )}
        </p>
      )}

    </article>
  );
}

function MetricRow({ metric }: { metric: CategoryMetric }) {
  const tone = metricTone(metric);

  return (
    <li className="category-card__metric">
      <span className={cn("category-card__dot", `category-card__dot_tone_${tone}`)} />
      <span className="category-card__metric-body">
        <span className="category-card__metric-text">{metric.summary}</span>
        {metric.evidence.length > 0 && <EvidenceLinks items={metric.evidence} limit={2} />}
      </span>
      <span className="category-card__metric-score num">
        {metric.normalizedScore === null ? "—" : formatScore(metric.normalizedScore)}
      </span>
    </li>
  );
}
