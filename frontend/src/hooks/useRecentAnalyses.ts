import { useEffect, useMemo, useState } from "react";

import { fetchAnalysisStatus, type AnalysisStatusResponse } from "../api/analyses";
import { ApiError } from "../api/http";
import type { MyRepository } from "../api/me";
import { forgetAnalysis, recentAnalysisId, withRecentAnalysis } from "../lib/recentAnalyses";

type RecentStatus = [repositoryId: string, status: AnalysisStatusResponse];

/**
 * Список кабинета с анализами, которые запускали из этого браузера (lib/recentAnalyses.ts).
 * Статусы спрашиваем один раз, когда список загрузился. Анализ, которого backend не знает
 * или который запускал другой пользователь (404), забываем.
 */
export function useRecentAnalyses(items: MyRepository[] | undefined): MyRepository[] {
  const [statuses, setStatuses] = useState<Record<string, AnalysisStatusResponse>>({});

  useEffect(() => {
    if (!items) return;
    const wanted = items.flatMap((item) => {
      const known = item.lastAnalysis !== null || item.activeAnalysisId !== null;
      const analysisId = known ? null : recentAnalysisId(item.repository.id);
      return analysisId ? [{ repositoryId: item.repository.id, analysisId }] : [];
    });
    if (wanted.length === 0) return;

    let cancelled = false;
    void Promise.all(
      wanted.map(({ repositoryId, analysisId }) =>
        fetchAnalysisStatus(analysisId).then(
          (status): RecentStatus | null => [repositoryId, status],
          (error: unknown) => {
            if (error instanceof ApiError && error.status === 404) forgetAnalysis(repositoryId);
            return null;
          },
        ),
      ),
    ).then((entries) => {
      if (!cancelled) {
        setStatuses(Object.fromEntries(entries.filter((entry): entry is RecentStatus => entry !== null)));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [items]);

  return useMemo(
    () => (items ?? []).map((item) => withRecentAnalysis(item, statuses[item.repository.id] ?? null)),
    [items, statuses],
  );
}
