import type { CategoryStatus } from "../api/common";
import { cn } from "../lib/classNames";
import { formatScore } from "../lib/format";
import { categoryStatusLabels } from "../lib/labels";
import { getScoreBand } from "../lib/scoreBands";
import "./ScoreBar.css";

interface ScoreBarProps {
  score: number | null;
  status: CategoryStatus;
  /** Что измеряет шкала — для скринридера. */
  label: string;
  /** m — шкала в отчёте, s — короткая полоска в списках. */
  size?: "m" | "s";
}

export function ScoreBar({ score, status, label, size = "m" }: ScoreBarProps) {
  const value = status === "measured" ? score : null;

  if (value === null) {
    return (
      <span
        className={cn("score-bar", `score-bar_size_${size}`, `score-bar_status_${status}`)}
        role="img"
        aria-label={`${label}: ${categoryStatusLabels[status]}`}
      />
    );
  }

  const width = Math.min(100, Math.max(0, value));
  return (
    <span
      className={cn("score-bar", `score-bar_size_${size}`, `score-bar_band_${getScoreBand(value)}`)}
      role="img"
      aria-label={`${label}: ${formatScore(value)} из 100`}
    >
      <span className="score-bar__fill" style={{ width: `${width}%` }} />
    </span>
  );
}
