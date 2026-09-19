import { Alert, Text } from "@gravity-ui/uikit";

import type { RepositoryReport } from "../../api/report";
import { cn } from "../../lib/classNames";
import { formatPoints, formatScore, formatShare, plural } from "../../lib/format";
import { getScoreBand } from "../../lib/scoreBands";
import { PreliminaryLabel, ScoreLimitLabel } from "../StatusLabels";
import { biggestLosses, buildFormula, isMeasured } from "./reportHelpers";
import "./ScoreCard.css";

export function ScoreCard({ report }: { report: RepositoryReport }) {
  const { score, analysis, categories, scoreDetails } = report;
  const measuredCount = categories.filter(isMeasured).length;
  const applicableCount = categories.filter((category) => category.status !== "not_applicable").length;

  return (
    <section className="card score-card">
      <div className="score-card__top">
        <div>
          <Text variant="body-1" color="secondary">
            Repo Health Score
          </Text>
          <div className="score-card__value-row">
            <span className={cn("score-card__value", "num", score === null && "score-card__value_empty")}>
              {score === null ? "нет оценки" : formatScore(score)}
            </span>
            {score !== null && (
              <Text variant="body-2" color="secondary" className="num">
                / 100
              </Text>
            )}
          </div>
        </div>
        <div className="score-card__labels">
          {analysis.isPreliminary && (
            <PreliminaryLabel hint={`Есть данные по ${scoreDetails.measuredWeight}% из ${scoreDetails.applicableWeight}% веса методики`} />
          )}
          {analysis.scoreLimit && <ScoreLimitLabel value={analysis.scoreLimit.value} />}
        </div>
      </div>

      <Text variant="body-2" color="secondary" className="score-card__note">
        {score === null
          ? "Ни по одной категории не удалось получить данные. Это не значит, что проект плохой: оценка появится, когда данные станут доступны."
          : analysis.isPreliminary
            ? `Есть данные по ${measuredCount} из ${applicableCount} ${plural(applicableCount, "категории", "категорий", "категорий")}${
                analysis.coverage === null ? "" : ` — это ${formatShare(analysis.coverage)} их веса`
              }. Когда появятся остальные, оценка может измениться в любую сторону.`
            : applicableCount === categories.length
              ? "Есть данные по всем категориям."
              : "Есть данные по всем категориям, которые относятся к этому репозиторию."}
      </Text>

      {analysis.scoreLimit && (
        <Alert
          className="score-card__limit"
          theme="danger"
          view="outlined"
          title={`Оценка ограничена: ${analysis.scoreLimit.uncappedScore} → ${analysis.scoreLimit.value}`}
          message={analysis.scoreLimit.summary}
        />
      )}

      {score !== null && <ScoreBreakdown report={report} />}
    </section>
  );
}

/** «Из чего сложились баллы»: ширина отрезка — вес категории, закрашено — набранные баллы. */
function ScoreBreakdown({ report }: { report: RepositoryReport }) {
  const segments = report.categories.filter(isMeasured).sort((a, b) => b.effectiveWeight - a.effectiveWeight);
  if (segments.length === 0) {
    return null;
  }

  const total = report.analysis.scoreLimit ? report.analysis.scoreLimit.uncappedScore : (report.score ?? 0);
  const rounded = Math.round(total);
  const formula = buildFormula(report.categories, report.scoreDetails.measuredWeight, total);
  const losses = biggestLosses(report.categories);

  return (
    <div className="breakdown">
      <div className="section__head">
        <Text variant="subheader-2">
          Из чего сложились {rounded} {plural(rounded, "балл", "балла", "баллов")}
        </Text>
        <Text variant="body-1" color="secondary">
          ширина — вес категории, закрашено — сколько баллов она принесла
        </Text>
      </div>

      <div className="breakdown__bar" role="list">
        {segments.map((segment) => (
          <div
            key={segment.code}
            role="listitem"
            className={cn("breakdown__segment", `breakdown__segment_band_${getScoreBand(segment.score)}`)}
            style={{ flexGrow: segment.effectiveWeight }}
            aria-label={`${segment.label}: ${formatPoints(segment.points)} из ${formatPoints(segment.effectiveWeight)}`}
          >
            <span
              className="breakdown__fill"
              style={{ width: `${(segment.points / segment.effectiveWeight) * 100}%` }}
            />
          </div>
        ))}
      </div>
      <div className="breakdown__labels" aria-hidden="true">
        {segments.map((segment) => (
          <div key={segment.code} className="breakdown__label" style={{ flexGrow: segment.effectiveWeight }}>
            <Text variant="body-1">{segment.label}</Text>
            <Text variant="body-1" color="secondary" className="num">
              {formatPoints(segment.points)} из {formatPoints(segment.effectiveWeight)}
            </Text>
          </div>
        ))}
      </div>

      <div className="breakdown__explain">
        {formula && (
          <Text variant="body-1" color="secondary" className="num">
            Как посчитано: {formula}
          </Text>
        )}
        {losses.length > 0 && (
          <Text variant="body-2">
            Больше всего баллов теряется в {losses.length === 1 ? "категории" : "категориях"}{" "}
            {losses.map((loss, index) => (
              <span key={loss.category.code}>
                {index > 0 && " и "}«{loss.category.label}» <span className="num">(−{formatPoints(loss.lost)})</span>
              </span>
            ))}
            . С них и начинаются рекомендации.
          </Text>
        )}
      </div>
    </div>
  );
}
