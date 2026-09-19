import { readItem, writeItem } from "./storage";

/** Запуск mock-анализа. Ход анализа вычисляется по времени с момента создания. */
export interface MockRun {
  id: string;
  repositoryId: string;
  createdAt: number;
}

/** Сколько длится mock-анализ: достаточно, чтобы на демо были видны все этапы. */
export const MOCK_ANALYSIS_DURATION_MS = 10_000;

const RUNS_KEY = "repo-health:mock-runs";

function readRuns(): MockRun[] {
  try {
    const parsed: unknown = JSON.parse(readItem(RUNS_KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as MockRun[]) : [];
  } catch {
    return [];
  }
}

export function findRun(id: string): MockRun | undefined {
  return readRuns().find((run) => run.id === id);
}

export function addRun(repositoryId: string, now = Date.now()): MockRun {
  const run: MockRun = { id: `an-${Math.random().toString(16).slice(2, 8)}`, repositoryId, createdAt: now };
  writeItem(RUNS_KEY, JSON.stringify([...readRuns(), run]));
  return run;
}

export function activeRunFor(repositoryId: string, now = Date.now()): MockRun | undefined {
  return readRuns().find((run) => run.repositoryId === repositoryId && now - run.createdAt < MOCK_ANALYSIS_DURATION_MS);
}

export function lastFinishedRunFor(repositoryId: string, now = Date.now()): MockRun | undefined {
  return readRuns()
    .filter((run) => run.repositoryId === repositoryId && now - run.createdAt >= MOCK_ANALYSIS_DURATION_MS)
    .sort((a, b) => b.createdAt - a.createdAt)[0];
}

export function finishedAt(run: MockRun): string {
  return new Date(run.createdAt + MOCK_ANALYSIS_DURATION_MS).toISOString();
}
