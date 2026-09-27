import { useEffect, useMemo, useState } from "react";

import { fetchAnalysisStatus, isAnalysisFinished, type AnalysisStatusResponse } from "../api/analyses";
import { ApiError } from "../api/http";
import type { MyRepository } from "../api/me";
import { forgetAnalysis, recentAnalysisId, withRecentAnalysis } from "../lib/recentAnalyses";

type RecentStatus = [repositoryId: string, status: AnalysisStatusResponse];

const POLL_INTERVAL_MS = 3_000;

/**
 * Список кабинета с анализами, которые запускали из этого браузера (lib/recentAnalyses.ts).
 * Идущие анализы опрашиваем до terminal-статуса, чтобы Score и ссылка на отчёт появились
 * в строке сами, без ручной перезагрузки. Анализ, которого backend не знает или который
 * запускал другой пользователь (404), забываем.
 */
export function useRecentAnalyses(items: MyRepository[] | undefined): MyRepository[] {
  const [statuses, setStatuses] = useState<Record<string, AnalysisStatusResponse>>({});

  useEffect(() => {
    if (!items) return;

    const wanted = items.flatMap((item) => {
      // Будущий API может сразу вернуть активный анализ; текущий main запоминает его в браузере.
      const analysisId =
        item.activeAnalysisId ?? (item.lastAnalysis === null ? recentAnalysisId(item.repository.id) : null);
      return analysisId ? [{ repositoryId: item.repository.id, analysisId }] : [];
    });
    if (wanted.length === 0) return;

    let cancelled = false;
    let timer: number | undefined;

    const refresh = (): void => {
      let retryAfterFailure = false;
      void Promise.all(
        wanted.map(({ repositoryId, analysisId }) =>
          fetchAnalysisStatus(analysisId).then(
            (status): RecentStatus | null => [repositoryId, status],
            (error: unknown) => {
              if (error instanceof ApiError && error.status === 404) {
                forgetAnalysis(repositoryId);
              } else {
                // Временная ошибка не должна оставлять строку навсегда в состоянии «идёт».
                retryAfterFailure = true;
              }
              return null;
            },
          ),
        ),
      ).then((entries) => {
        if (cancelled) return;
        const received = entries.filter((entry): entry is RecentStatus => entry !== null);
        if (received.length > 0) {
          setStatuses((previous) => ({ ...previous, ...Object.fromEntries(received) }));
        }
        if (retryAfterFailure || received.some(([, status]) => !isAnalysisFinished(status.status))) {
          timer = window.setTimeout(refresh, POLL_INTERVAL_MS);
        }
      });
    };

    refresh();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [items]);

  return useMemo(
    () => (items ?? []).map((item) => withRecentAnalysis(item, statuses[item.repository.id] ?? null)),
    [items, statuses],
  );
}
