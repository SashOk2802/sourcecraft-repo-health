import { Button, Text } from "@gravity-ui/uikit";

import { ApiError, describeError } from "../../api/http";
import { fetchPublicHistory, type ScorePoint } from "../../api/publicHistory";
import type { RepositoryReport } from "../../api/report";
import { dataOf, useAsync } from "../../hooks/useAsync";
import { cn } from "../../lib/classNames";
import { formatDate, formatDateTimeCompact, formatPoints, formatScore, formatShare } from "../../lib/format";
import { getScoreBand, SCORE_BAND_LIMITS } from "../../lib/scoreBands";
import { chartSegments, summarizeHistory } from "../../lib/scoreHistory";
import { LoadingNote } from "../PageNotes";
import "./ScoreHistory.css";

const CHART = { width: 560, height: 150, padding: 14 };

/*
 * Динамика Score публичного репозитория — только по сохранённым снимкам, которые отдаёт
 * GET /api/v1/public/repositories/{org}/{repo}/history. Ничего не дорисовываем: пустая
 * история так и называется, точка без оценки — разрыв линии, другая версия методики
 * в изменение не входит.
 */
export function ScoreHistory({ report }: { report: RepositoryReport }) {
  const { organizationSlug, repositorySlug } = report.repository;
  const [state, reload] = useAsync(
    () => fetchPublicHistory(organizationSlug, repositorySlug),
    [organizationSlug, repositorySlug],
  );
  const points = dataOf(state);

  return (
    <section className="card score-history" aria-labelledby="score-history-title">
      <div className="section__head">
        <Text variant="subheader-2" as="h2" id="score-history-title">
          Динамика Score
        </Text>
        <Text variant="body-1" color="secondary">
          по сохранённым оценкам, до 20 последних
        </Text>
      </div>

      {!points && state.status === "loading" && <LoadingNote>Загружаем историю</LoadingNote>}
      {!points && state.status === "error" && <HistoryError error={state.error} onRetry={reload} />}
      {points && <HistoryBody points={points} />}
    </section>
  );
}

function HistoryError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  if (error instanceof ApiError && error.status === 404) {
    return (
      <Text variant="body-2" color="secondary">
        История есть только у репозиториев, которые SourceCraft сейчас подтверждает как публичные.
      </Text>
    );
  }
  return (
    <div className="score-history__error">
      <Text variant="body-2" color="secondary">
        Не удалось загрузить историю. {describeError(error)}
      </Text>
      <Button view="outlined" size="s" onClick={onRetry}>
        Попробовать ещё раз
      </Button>
    </div>
  );
}

function HistoryBody({ points }: { points: ScorePoint[] }) {
  if (points.length === 0) {
    return (
      <Text variant="body-2" color="secondary">
        История пока пуста: сохранённых оценок этого репозитория ещё нет. Точки появятся после пересчётов по
        расписанию.
      </Text>
    );
  }

  const summary = summarizeHistory(points);
  const first = summary.comparable[0];
  const last = summary.comparable[summary.comparable.length - 1];

  return (
    <div className="score-history__body">
      <Text variant="body-2">
        {summary.delta === null ? (
          points.length === 1 ? (
            "Пока одна оценка — динамика появится после следующего пересчёта."
          ) : (
            "Сравнивать пока не с чем: оценок одной версии методики меньше двух."
          )
        ) : (
          <>
            {summary.delta === 0 ? "Без изменений" : `${summary.delta > 0 ? "+" : "−"}${formatPoints(Math.abs(summary.delta))}`}{" "}
            за период с {formatDate(first.analyzedAt)} по {formatDate(last.analyzedAt)}: с{" "}
            <span className="num">{formatScore(first.score as number)}</span> до{" "}
            <span className="num">{formatScore(last.score as number)}</span>.
          </>
        )}
      </Text>
      {summary.versionChanged && (
        <Text variant="body-1" color="secondary">
          Методика за это время менялась: изменение считаем только по оценкам {summary.latestVersion}.
        </Text>
      )}

      <HistoryChart points={points} />

      <table className="score-history__table">
        <thead>
          <tr>
            <th scope="col">Дата</th>
            <th scope="col">Score</th>
            <th scope="col">Полнота данных</th>
            <th scope="col">Оценка</th>
            <th scope="col">Методика</th>
          </tr>
        </thead>
        <tbody>
          {[...points].reverse().map((point) => (
            <tr key={point.analyzedAt}>
              <td>{formatDateTimeCompact(point.analyzedAt)}</td>
              <td className={cn("num", point.score !== null && `score-history__score_band_${getScoreBand(point.score)}`)}>
                {point.score === null ? "нет оценки" : formatScore(point.score)}
              </td>
              <td className="num">{point.coverage === null ? "—" : formatShare(point.coverage)}</td>
              <td>{point.status === "partial" ? "предварительная" : "полная"}</td>
              <td>{point.methodologyVersion}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Линия Score 0–100 с порогами полос; предварительная оценка — полый кружок. */
function HistoryChart({ points }: { points: ScorePoint[] }) {
  const segments = chartSegments(points, CHART.width, CHART.height, CHART.padding);
  const yOf = (score: number) => CHART.padding + (1 - score / 100) * (CHART.height - CHART.padding * 2);

  return (
    <svg
      className="score-history__chart"
      viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      role="img"
      aria-label={`График Score по ${points.length} сохранённым оценкам`}
    >
      {[SCORE_BAND_LIMITS.low, SCORE_BAND_LIMITS.high].map((limit) => (
        <line
          key={limit}
          className="score-history__threshold"
          x1={CHART.padding}
          x2={CHART.width - CHART.padding}
          y1={yOf(limit)}
          y2={yOf(limit)}
        />
      ))}
      {segments.map((segment, index) => (
        <polyline
          key={index}
          className="score-history__line"
          points={segment.map((item) => `${item.x},${item.y}`).join(" ")}
        />
      ))}
      {segments.flat().map((item) => (
        <circle
          key={item.point.analyzedAt}
          className={cn(
            "score-history__dot",
            `score-history__dot_band_${getScoreBand(item.point.score as number)}`,
            item.point.status === "partial" && "score-history__dot_partial",
          )}
          cx={item.x}
          cy={item.y}
          r={4.5}
        >
          <title>
            {`${formatDate(item.point.analyzedAt)}: ${formatScore(item.point.score as number)}${
              item.point.status === "partial" ? ", предварительная" : ""
            }`}
          </title>
        </circle>
      ))}
    </svg>
  );
}
