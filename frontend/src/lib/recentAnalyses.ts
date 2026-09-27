import { isAnalysisFinished, type AnalysisStatusResponse } from "../api/analyses";
import type { MyRepository } from "../api/me";

/*
 * Какой анализ пользователь запускал для репозитория из этого браузера. GET /api/v1/me/repositories
 * на main не отдаёт последний анализ, и без этой памяти кабинет после проверки писал бы
 * «ещё не проверяли». Храним только id анализа, а статус и Score всё равно спрашиваем у backend:
 * статус видит только тот, кто анализ запускал, поэтому чужого сюда не попадёт.
 */

const STORAGE_KEY = "rh-recent-analyses";

type Recent = Record<string, string>;

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    // Хранилище выключено или страница открыта не в браузере — просто не помним.
    return null;
  }
}

function read(storage: Storage | null): Recent {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(STORAGE_KEY) ?? "{}");
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Recent) : {};
  } catch {
    return {};
  }
}

function write(storage: Storage | null, recent: Recent): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(recent));
  } catch {
    // Хранилище переполнено или запрещено — не страшно: это только подсказка для кабинета.
  }
}

export function rememberAnalysis(repositoryId: string, analysisId: string, storage = browserStorage()): void {
  write(storage, { ...read(storage), [repositoryId]: analysisId });
}

export function recentAnalysisId(repositoryId: string, storage = browserStorage()): string | null {
  const id = read(storage)[repositoryId];
  return typeof id === "string" && id !== "" ? id : null;
}

export function forgetAnalysis(repositoryId: string, storage = browserStorage()): void {
  const recent = read(storage);
  if (!(repositoryId in recent)) return;
  delete recent[repositoryId];
  write(storage, recent);
}

/**
 * Строка кабинета с последним опрошенным анализом. Идущий показываем ссылкой на ход,
 * завершённый — датой, Score и отчётом. Ответ статуса должен уметь заменить устаревший
 * activeAnalysisId из списка: иначе строка осталась бы «идёт анализ» до ручного обновления.
 */
export function withRecentAnalysis(item: MyRepository, recent: AnalysisStatusResponse | null): MyRepository {
  if (recent === null) {
    return item;
  }
  if (!isAnalysisFinished(recent.status)) {
    return { ...item, lastAnalysis: null, activeAnalysisId: recent.id };
  }
  return {
    ...item,
    activeAnalysisId: null,
    lastAnalysis: {
      id: recent.id,
      status: recent.status,
      analyzedAt: recent.finishedAt ?? recent.createdAt ?? null,
      score: recent.score,
      isPreliminary: recent.isPreliminary ?? false,
    },
  };
}
