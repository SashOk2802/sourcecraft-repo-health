import { ThemeProvider } from "@gravity-ui/uikit";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";

import { browserStorage, readThemePreference, saveThemePreference, type ThemePreference } from "../lib/theme";

/*
 * Выбор темы: как в системе, светлая или тёмная. «Как в системе» Gravity UI
 * отслеживает сам — при смене темы в ОС интерфейс перекрашивается без перезагрузки.
 * На печать отчёт всегда уходит светлым: тёмный лист бумаге ни к чему.
 */

interface ThemeChoice {
  preference: ThemePreference;
  setPreference: (next: ThemePreference) => void;
}

const ThemeChoiceContext = createContext<ThemeChoice | null>(null);

export function ThemeChoiceProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => readThemePreference(browserStorage()));
  const printing = usePrinting();

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    saveThemePreference(browserStorage(), next);
  }, []);

  const value = useMemo(() => ({ preference, setPreference }), [preference, setPreference]);

  return (
    <ThemeChoiceContext.Provider value={value}>
      <ThemeProvider theme={printing ? "light" : preference}>{children}</ThemeProvider>
    </ThemeChoiceContext.Provider>
  );
}

export function useThemeChoice(): ThemeChoice {
  const value = useContext(ThemeChoiceContext);
  if (!value) {
    throw new Error("useThemeChoice можно вызывать только внутри ThemeChoiceProvider");
  }
  return value;
}

/**
 * Идёт ли печать. Перерисовка нужна до того, как браузер снимет страницу,
 * поэтому обновление выполняется синхронно.
 */
function usePrinting(): boolean {
  const [printing, setPrinting] = useState(false);

  useEffect(() => {
    const start = (): void => flushSync(() => setPrinting(true));
    const finish = (): void => setPrinting(false);
    window.addEventListener("beforeprint", start);
    window.addEventListener("afterprint", finish);
    return () => {
      window.removeEventListener("beforeprint", start);
      window.removeEventListener("afterprint", finish);
    };
  }, []);

  return printing;
}
