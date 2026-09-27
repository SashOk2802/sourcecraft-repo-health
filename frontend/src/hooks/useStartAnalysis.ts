import { useState } from "react";

import { startAnalysis } from "../api/analyses";
import { rememberAnalysis } from "../lib/recentAnalyses";
import { navigate } from "../router";
import { paths } from "../routes";

/** Запускает анализ и открывает страницу с его ходом; кабинет запомнит его для строки репозитория. */
export function useStartAnalysis() {
  const [startingId, setStartingId] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);

  async function start(repositoryId: string): Promise<void> {
    setStartingId(repositoryId);
    setError(null);
    try {
      const started = await startAnalysis(repositoryId);
      rememberAnalysis(repositoryId, started.id);
      navigate(paths.analysis(started.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
      setStartingId(null);
    }
  }

  return { start, startingId, error };
}
