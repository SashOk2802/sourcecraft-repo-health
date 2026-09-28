import { Text } from "@gravity-ui/uikit";
import type { CSSProperties } from "react";

import type { CategoryStatus, Evidence } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
import { describeCategory } from "../../lib/categoryMeaning";
import { cn } from "../../lib/classNames";
import { formatPoints, formatScore } from "../../lib/format";
import { reasonDetail } from "../../lib/reasonCodes";
import { getScoreBand, SCORE_BAND_LIMITS } from "../../lib/scoreBands";
import { CategoryIcon } from "../CategoryIcon";
import { CategoryStatusLabel } from "../StatusLabels";
import { EvidenceLinks } from "./EvidenceLinks";
import {
  metricEvidence,
  metricLabel,
  metricTone,
  metricValueText,
  summaryWithoutScore,
  unmeasuredLinks,
  visibleMetrics,
  withoutRepeatedLinks,
} from "./reportHelpers";
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
          каждая часть проверяется по нескольким признакам: {SCORE_BAND_LIMITS.high} и выше — хорошо,{" "}
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
  const metrics = visibleMetrics(category);
  const evidence = withoutRepeatedLinks(metrics.map((metric) => metricEvidence(metric, category.evidence)));
  // Оценка уже стоит в шапке карточки: «Оценка документации: 85/100.» из summary не повторяем.
  const summary = score === null ? category.summary : summaryWithoutScore(category.summary);
  const detail = score === null ? reasonDetail(category.summary, category.reason) : null;
  const links = score === null ? unmeasuredLinks(category) : [];

  return (
    <article id={`category-${category.code}`} className="category-card" style={{ "--rh-step": order } as CSSProperties}>
      <header className="category-card__head">
        <CategoryIcon code={category.code} band={band} />
        <Text variant="subheader-1" as="h3" className="category-card__name">
          {category.label}
        </Text>
        {score !== null && (
          <span className={cn("category-card__score", band && `category-card__score_band_${band}`)}>
            <span className="num">{formatScore(score)}</span>
          </span>
        )}
      </header>

      {meaning && (
        <Text variant="body-1" color="hint" className="category-card__meaning">
          {meaning}
        </Text>
      )}

      <div className="category-card__status">
        {score === null && <CategoryStatusLabel status={category.status as Exclude<CategoryStatus, "measured">} />}
        <Text variant="body-1" color="hint">
          {category.effectiveWeight === null
            ? "в оценку не вошло — её доля перешла к остальным частям"
            : `влияет на оценку на ${formatPoints(category.effectiveWeight)}%`}
        </Text>
      </div>

      {/* Без оценки главное — почему её нет: summary backend и объяснение причины. */}
      {(score === null || metrics.length === 0) && summary !== "" && (
        <p className="category-card__reason">
          <Text variant="body-2">{summary}</Text>{" "}
          {detail !== null && (
            <Text variant="body-2" color="secondary">
              {detail}
            </Text>
          )}
        </p>
      )}

      {links.length > 0 && <EvidenceLinks items={links} />}

      {metrics.length > 0 && (
        <ul className="category-card__metrics">
          {metrics.map((metric, index) => (
            <MetricRow key={metric.code} metric={metric} evidence={evidence[index]} />
          ))}
        </ul>
      )}

    </article>
  );
}

function MetricRow({ metric, evidence }: { metric: CategoryMetric; evidence: Evidence[] }) {
  const tone = metricTone(metric);

  return (
    <li className="category-card__metric">
      <span className={cn("category-card__dot", `category-card__dot_tone_${tone}`)} />
      <span className="category-card__metric-body">
        <span className="category-card__metric-text">{metricLabel(metric)}</span>
        {evidence.length > 0 && <EvidenceLinks items={evidence} limit={2} />}
      </span>
      <span className="category-card__metric-score num">{metricValueText(metric)}</span>
    </li>
  );
}
