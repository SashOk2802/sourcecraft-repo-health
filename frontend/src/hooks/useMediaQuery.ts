import { useSyncExternalStore } from "react";

/** Совпадает ли медиазапрос сейчас; перерисовывает при повороте экрана и смене ширины окна. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia?.(query);
      list?.addEventListener("change", onChange);
      return () => list?.removeEventListener("change", onChange);
    },
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  );
}
