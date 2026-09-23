import { useSyncExternalStore } from "react";

import { sourceRouter, type Section, type Source } from "../api/dataSource";

/** Откуда пришли данные раздела — чтобы страница пометила демо. null — ещё не загружали. */
export function useSectionSource(section: Section): Source | null {
  return useSyncExternalStore(sourceRouter.subscribe, () => sourceRouter.currentSource(section));
}
