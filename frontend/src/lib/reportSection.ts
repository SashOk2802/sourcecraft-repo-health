import { useSyncExternalStore } from "react";

/**
 * Раздел шапки, к которому относится страница анализа. Закрытый или внутренний репозиторий
 * в рейтинг не попадает — его отчёт живёт в «Моих репозиториях», публичный — в рейтинге.
 * null — пока неизвестно: ни один пункт шапки не подсвечен.
 */
export type ReportSection = "leaderboard" | "myRepositories";

let current: ReportSection | null = null;
const listeners = new Set<() => void>();

/** Страница анализа сообщает шапке свой раздел; при уходе со страницы — null. */
export function setReportSection(next: ReportSection | null): void {
  if (next === current) return;
  current = next;
  for (const listener of listeners) listener();
}

export function useReportSection(): ReportSection | null {
  return useSyncExternalStore(subscribe, () => current);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

interface ReportFacts {
  /** Откуда пришли по ссылке: из кабинета или рейтинга. */
  origin: ReportSection | null;
  /** Отчёт загружен; badgeAvailable backend ставит только публичному репозиторию из каталога. */
  report: { badgeAvailable: boolean } | null;
  /** Анализ идёт или не удался: ход и ошибку backend показывает только тому, кто запускал. */
  personalRun: boolean;
  /** Статус анализа не получен (нет входа, чужой или несуществующий анализ). */
  unavailable: boolean;
}

/** К какому разделу отнести страницу анализа. */
export function resolveReportSection({ origin, report, personalRun, unavailable }: ReportFacts): ReportSection | null {
  // Пришли из кабинета или из рейтинга — остаёмся в том же разделе, в том числе после «Проверить снова».
  if (origin) return origin;
  if (report) return report.badgeAvailable ? "leaderboard" : "myRepositories";
  if (personalRun) return "myRepositories";
  // Отчёт недоступен — страница предлагает вернуться к рейтингу.
  if (unavailable) return "leaderboard";
  return null;
}
