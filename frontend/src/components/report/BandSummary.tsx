import type { CSSProperties } from "react";

import type { ReportCategory } from "../../api/report";
import { cn } from "../../lib/classNames";
import { plural } from "../../lib/format";
import { summarizeBands } from "./reportHelpers";
import "./BandSummary.css";

/*
 * Сводка по частям проекта одной строкой и полосой из сегментов:
 * «2 проблемы · 1 стоит посмотреть · 2 хорошо». Приём — как консенсус
 * аналитиков в виджете акций Яндекса: сначала плохое, потом хорошее,
 * а полоса из отдельных сегментов считывается раньше, чем числа.
 */
export function BandSummary({ categories }: { categories: ReportCategory[] }) {
  const summary = summarizeBands(categories);
  const segments = [
    ...Array<"low">(summary.low).fill("low"),
    ...Array<"mid">(summary.mid).fill("mid"),
    ...Array<"high">(summary.high).fill("high"),
    ...Array<"missing">(summary.missing).fill("missing"),
  ];
  if (segments.length === 0) {
    return null;
  }

  const counts = [
    { key: "low", value: summary.low, label: plural(summary.low, "проблема", "проблемы", "проблем") },
    { key: "mid", value: summary.mid, label: "стоит посмотреть" },
    { key: "high", value: summary.high, label: "хорошо" },
  ] as const;

  return (
    <div className="band-summary">
      <div className="band-summary__counts">
        {counts.map((count) => (
          <span
            key={count.key}
            className={cn("band-summary__count", count.value > 0 && `band-summary__count_band_${count.key}`)}
          >
            <b className="num">{count.value}</b> {count.label}
          </span>
        ))}
        {summary.missing > 0 && (
          <span className="band-summary__count band-summary__count_missing">
            <b className="num">{summary.missing}</b> без данных
          </span>
        )}
      </div>
      <div
        className="band-summary__bar"
        role="img"
        aria-label={`Частей проекта: проблем ${summary.low}, стоит посмотреть ${summary.mid}, хорошо ${summary.high}, без данных ${summary.missing}`}
      >
        {segments.map((band, index) => (
          <i
            key={index}
            className={cn("band-summary__segment", `band-summary__segment_band_${band}`)}
            style={{ "--rh-step": index } as CSSProperties}
          />
        ))}
      </div>
    </div>
  );
}
