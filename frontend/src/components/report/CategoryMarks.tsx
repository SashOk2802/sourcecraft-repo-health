import { Text } from "@gravity-ui/uikit";
import type { CSSProperties } from "react";

import type { CategoryStatus } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
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
          внутри категории — её метрики: {SCORE_BAND_LIMITS.high} и выше хорошо, {SCORE_BAND_LIMITS.low}–
          {SCORE_BAND_LIMITS.high - 1} предупреждение, ниже {SCORE_BAND_LIMITS.low} проблема; серая точка —
          справочная метрика, её не оценивают
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

  return (
    <article className="category-card" style={{ "--rh-step": order } as CSSProperties}>
      <header className="category-card__head">
        <Text variant="subheader-1" as="h3">
          {category.label}
        </Text>
        <Text variant="body-1" color="hint" className="category-card__weight">
          вес {formatPoints(category.weight)}%
          {category.effectiveWeight !== null && category.effectiveWeight !== category.weight
            ? ` → ${formatPoints(category.effectiveWeight)}%`
            : ""}
        </Text>
        <span className={cn("category-card__score", band && `category-card__score_band_${band}`)}>
          {score !== null ? (
            <span className="num">{formatScore(score)}</span>
          ) : (
            <CategoryStatusLabel status={category.status as Exclude<CategoryStatus, "measured">} />
          )}
        </span>
      </header>

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

      {category.effectiveWeight === null && (
        <Text variant="body-1" color="hint" className="category-card__excluded">
          В расчёт не входит: вес распределился на остальные категории.
        </Text>
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
