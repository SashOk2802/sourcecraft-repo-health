import { Alert, Text } from "@gravity-ui/uikit";

import type { RepositoryReport } from "../../api/report";
import { useCountUp } from "../../hooks/useCountUp";
import { cn } from "../../lib/classNames";
import { formatPoints, formatScore, formatShare, plural } from "../../lib/format";
import { getScoreBand } from "../../lib/scoreBands";
import { scoreVerdict } from "../../lib/verdict";
import { PreliminaryLabel, ScoreLimitLabel } from "../StatusLabels";
import { CategoryRadar } from "./CategoryRadar";
import { biggestLosses, buildFormula, isMeasured } from "./reportHelpers";
import "./ScoreCard.css";

/*
 * Шапка отчёта: слева радар категорий, справа сам Score и пояснение к нему.
 * Радар заменил полосу вклада — вес категории виден по длине луча,
 * а категория без данных остаётся без вершины и не превращается в ноль.
 */
export function ScoreCard({ report }: { report: RepositoryReport }) {
  const { score, analysis, categories, scoreDetails } = report;
  // Число набегает от нуля, пока собирается радар.
  const shownScore = useCountUp(score);
  const verdict = scoreVerdict(score, analysis.isPreliminary);
  const measuredCount = categories.filter(isMeasured).length;
  const applicableCount = categories.filter((category) => category.status !== "not_applicable").length;

  return (
    <section className="card score-card">
      <div className="score-card__layout">
        <div className="score-card__chart">
          <CategoryRadar categories={categories} />
        </div>

        <div className="score-card__side">
          <Text variant="header-2" as="h2" className="score-card__verdict">
            {verdict.title}
          </Text>
          <Text variant="body-2" color="secondary" className="score-card__verdict-note">
            {verdict.note}
          </Text>

          <div className="score-card__value-row">
            <span
              className={cn(
                "score-card__value",
                "num",
                score === null ? "score-card__value_empty" : `score-card__value_band_${getScoreBand(score)}`,
              )}
            >
              {score === null ? "нет оценки" : formatScore(shownScore ?? score)}
            </span>
            {score !== null && (
              <Text variant="body-2" color="secondary" className="score-card__of">
                из 100 — это Repo Health Score
              </Text>
            )}
          </div>

          <div className="score-card__labels">
            {/* Пока оценки нет, метка «предварительная» только путает: предварять нечего. */}
            {analysis.isPreliminary && score !== null && (
              <PreliminaryLabel
                hint={`Есть данные по ${scoreDetails.measuredWeight}% из ${scoreDetails.applicableWeight}% веса методики`}
              />
            )}
            {analysis.scoreLimit && <ScoreLimitLabel value={analysis.scoreLimit.value} />}
          </div>

          {/* Когда оценки нет, вердикт уже всё сказал — второй раз не повторяем. */}
          {score !== null && (
          <Text variant="body-2" color="secondary" className="score-card__note">
            {analysis.isPreliminary
                ? `Есть данные по ${measuredCount} из ${applicableCount} ${plural(applicableCount, "части", "частей", "частей")} проекта${
                    analysis.coverage === null ? "" : ` — это ${formatShare(analysis.coverage)} от того, что влияет на оценку`
                  }. Когда появятся остальные, оценка может измениться в любую сторону.`
                : applicableCount === categories.length
                  ? "Есть данные по всем частям проекта."
                  : "Есть данные по всем частям, которые относятся к этому репозиторию."}
          </Text>
          )}

          {analysis.scoreLimit && (
            <Alert
              className="score-card__limit"
              theme="danger"
              view="outlined"
              title={`Оценка ограничена: ${analysis.scoreLimit.uncappedScore} → ${analysis.scoreLimit.value}`}
              message={analysis.scoreLimit.summary}
            />
          )}

          {score !== null && <ScoreExplain report={report} />}
        </div>
      </div>
    </section>
  );
}

/** Как получился Score: формула из оценок backend и категории с наибольшими потерями. */
function ScoreExplain({ report }: { report: RepositoryReport }) {
  const total = report.analysis.scoreLimit ? report.analysis.scoreLimit.uncappedScore : (report.score ?? 0);
  const formula = buildFormula(report.categories, report.scoreDetails.measuredWeight, total);
  const losses = biggestLosses(report.categories);

  if (!formula && losses.length === 0) {
    return null;
  }

  return (
    <div className="score-card__explain">
      {formula && (
        <Text variant="body-1" color="hint" className="num">
          Как посчитали: {formula}
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
  );
}
