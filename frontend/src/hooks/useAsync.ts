import { useEffect, useState, type DependencyList } from "react";

/** Состояние загрузки. previous — последние данные, чтобы не мигать пустым экраном при перезагрузке. */
export type AsyncState<T> =
  | { status: "loading"; previous: T | undefined }
  | { status: "error"; error: Error; previous: T | undefined }
  | { status: "success"; data: T };

/**
 * Загружает данные при изменении deps и возвращает [состояние, перезагрузить].
 * Ответ устаревшего запроса отбрасывается, если deps успели поменяться.
 */
export function useAsync<T>(load: () => Promise<T>, deps: DependencyList): [AsyncState<T>, () => void] {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading", previous: undefined });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((current) => ({ status: "loading", previous: dataOf(current) }));

    load().then(
      (data) => {
        if (!cancelled) setState({ status: "success", data });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState((current) => ({ status: "error", error: toError(error), previous: dataOf(current) }));
        }
      },
    );

    return () => {
      cancelled = true;
    };
    // load пересоздаётся на каждом рендере, поэтому зависимости передаются явно.
  }, [...deps, attempt]);

  return [state, () => setAttempt((value) => value + 1)];
}

export function dataOf<T>(state: AsyncState<T>): T | undefined {
  return state.status === "success" ? state.data : state.previous;
}

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}
