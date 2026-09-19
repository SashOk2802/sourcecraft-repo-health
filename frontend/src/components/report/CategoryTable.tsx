import { Text } from "@gravity-ui/uikit";

import type { CategoryStatus } from "../../api/common";
import type { CategoryMetric, ReportCategory } from "../../api/report";
import { cn } from "../../lib/classNames";
import { formatPoints, formatScore } from "../../lib/format";
import { describeReason } from "../../lib/reasonCodes";
import { getScoreBand, SCORE_BAND_LIMITS } from "../../lib/scoreBands";
import { ScoreBar } from "../ScoreBar";
import { CategoryStatusLabel } from "../StatusLabels";
import { EvidenceLinks } from "./EvidenceLinks";
import "./CategoryTable.css";

export function CategoryTable({ categories }: { categories: ReportCategory[] }) {
  return (
    <section className="section">
      <div className="section__head">
        <Text variant="subheader-2" as="h2">
          Категории
        </Text>
        <Text variant="body-1" color="secondary">
          красным — ниже {SCORE_BAND_LIMITS.low}, жёлтым — {SCORE_BAND_LIMITS.low}–{SCORE_BAND_LIMITS.high - 1}
        </Text>
      </div>

      <table className="category-table">
        <thead>
          <tr>
            <th scope="col">Категория</th>
            <th scope="col" className="category-table__score-head">
              Оценка
            </th>
            <th scope="col" className="category-table__weight-head">
              Вес
            </th>
            <th scope="col">Что повлияло</th>
          </tr>
        </thead>
        <tbody>
          {categories.map((category) => (
            <CategoryRow key={category.code} category={category} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function CategoryRow({ category }: { category: ReportCategory }) {
  const score = category.status === "measured" ? category.score : null;
  const band = score !== null ? getScoreBand(score) : null;

  return (
    <tr className={cn("category-table__row", band && `category-table__row_band_${band}`)}>
      <th scope="row" className="category-table__name">
        {category.label}
      </th>

      <td className="category-table__score">
        {score !== null ? (
          <span className="num">{formatScore(score)}</span>
        ) : (
          <CategoryStatusLabel status={category.status as Exclude<CategoryStatus, "measured">} />
        )}
        <span className="category-table__scale">
          <ScoreBar score={category.score} status={category.status} label={category.label} size="s" />
        </span>
      </td>

      <td className="category-table__weight num">
        {formatPoints(category.weight)}%
        {category.effectiveWeight === null ? (
          <Text variant="body-1" color="secondary" className="category-table__weight-note">
            не участвует в расчёте
          </Text>
        ) : (
          category.effectiveWeight !== category.weight && (
            <Text variant="body-1" color="secondary" className="category-table__weight-note num">
              → {formatPoints(category.effectiveWeight)}%
            </Text>
          )
        )}
      </td>

      <td className="category-table__why">
        <Text variant="body-2">{category.summary}</Text>
        {category.reason && (
          <Text variant="body-1" color="secondary" className="category-table__reason">
            {describeReason(category.reason)}
          </Text>
        )}
        {category.evidence.map((metric) => (
          <MetricRow key={metric.code} metric={metric} />
        ))}
      </td>
    </tr>
  );
}

function MetricRow({ metric }: { metric: CategoryMetric }) {
  return (
    <div className="category-table__metric">
      <Text variant="body-1" color="secondary">
        {metric.summary}
      </Text>
      <EvidenceLinks items={metric.evidence} />
    </div>
  );
}
